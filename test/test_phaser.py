"""
Unit tests for the phasing pipeline: processor.filter_intermethod_cfg's
dead-end tagging, processor.flatten_intermethod_cfg, and phaser.
build_phase_tree. Exercises the rules in PHASING_RULES.md against small,
hand-built CFG fixtures rather than a live Joern session -- see
PHASING_RULES.md for what each rule means and why.

Run with: poetry run python -m unittest discover -s test
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "processor"))

from processor import filter_intermethod_cfg, flatten_intermethod_cfg  # noqa: E402
from phaser import build_phase_tree  # noqa: E402

# --------------------------------------------------------------------------
# Fixture helpers -- small enough that hand-tracing the expected result is
# feasible, unlike a real Joern-extracted graph.
# --------------------------------------------------------------------------

def node(id_, type_, callee=None, caller=None, dead=False):
    d = {"id": id_, "type": type_}
    if callee is not None:
        d["calleeFullName"] = callee
    if caller is not None:
        d["callerMethod"] = caller
    if dead:
        d["deadEnd"] = True
    return d


def edge(frm, to, type_="sequence"):
    return {"from": frm, "to": to, "type": type_}


def phase_names(tree: dict, flattened: dict) -> list[list[str]]:
    """Each phase's member nodes, as calleeFullName, in order -- for
    readable assertions instead of comparing raw (cloned) ids."""
    by_id = {n["id"]: n["calleeFullName"] for n in flattened["nodes"]}
    return [[by_id[n] for n in phase["nodes"]] for phase in tree["phases"]]


def all_names(tree: dict, flattened: dict) -> set[str]:
    return {name for phase in phase_names(tree, flattened) for name in phase}


# --------------------------------------------------------------------------
# filter_intermethod_cfg: dead-end tagging (feeds phaser's R3/R4)
# --------------------------------------------------------------------------

class DeadEndTaggingTests(unittest.TestCase):
    def test_throw_guard_tagged_dead_end(self):
        # if (amount <= 0) throw new IllegalArgumentException(...);
        # ledger.debit(amount);
        raw = {
            "entryPoint": "withdraw",
            "nodes": [
                node("e", "entry", "withdraw"),
                node("op_guard", "call", "<operator>.greaterEqualsThan", "withdraw"),
                node("c_ctor", "call", "IllegalArgumentException.<init>", "withdraw"),
                node("op_throw", "call", "<operator>.throw", "withdraw"),
                node("c_debit", "call", "Ledger.debit", "withdraw"),
            ],
            "edges": [
                edge("e", "op_guard"),
                edge("op_guard", "c_ctor"),
                edge("c_ctor", "op_throw"),
                # Joern's cfgNext quirk (DESIGN.md §4.3/§8.6): the throw's
                # own successor is wired as if it fell through.
                edge("op_throw", "c_debit"),
                edge("op_guard", "c_debit"),
            ],
        }
        filtered = filter_intermethod_cfg(raw)
        self.assertNotIn("deadEndIds", filtered)
        dead_ids = {n["id"] for n in filtered["nodes"] if n.get("deadEnd")}
        self.assertEqual(dead_ids, {"c_ctor"})

    def test_no_noise_means_no_dead_ends_and_no_key(self):
        raw = {
            "entryPoint": "run",
            "nodes": [node("e", "entry", "run"), node("c", "call", "Logger.log", "run")],
            "edges": [edge("e", "c")],
        }
        filtered = filter_intermethod_cfg(raw)
        self.assertNotIn("deadEndIds", filtered)
        self.assertFalse(any(n.get("deadEnd") for n in filtered["nodes"]))


# --------------------------------------------------------------------------
# R3/R4: guard clauses
# --------------------------------------------------------------------------

class GuardClauseTests(unittest.TestCase):
    def test_dead_end_sibling_does_not_fragment_live_path(self):
        # validate(); if (invalid) throw ...; commit() -- the throw must
        # not split validate() away from commit().
        filtered_cfg = {
            "entryPoint": "run",
            "nodes": [
                node("e", "entry", "run"),
                node("c_validate", "call", "Account.validate", "run"),
                node("c_guard", "call", "Exception.<init>", "run", dead=True),
                node("c_commit", "call", "Account.commit", "run"),
                node("leaf_validate", "leaf", "Account.validate"),
                node("leaf_guard", "leaf", "Exception.<init>"),
                node("leaf_commit", "leaf", "Account.commit"),
            ],
            "edges": [
                edge("e", "c_validate"),
                edge("c_validate", "c_guard"),
                edge("c_validate", "c_commit"),
                edge("c_validate", "leaf_validate", "invoke"),
                edge("c_guard", "leaf_guard", "invoke"),
                edge("c_commit", "leaf_commit", "invoke"),
            ],
        }
        flattened = flatten_intermethod_cfg(filtered_cfg)
        tree = build_phase_tree(flattened)

        self.assertEqual(len(tree["phases"]), 1)
        self.assertEqual(
            set(phase_names(tree, flattened)[0]),
            {"Account.validate", "Exception.<init>", "Account.commit"},
        )


# --------------------------------------------------------------------------
# R5/R6: a call's contribution depends on its own resolved phase count
# --------------------------------------------------------------------------

class CallResolutionTests(unittest.TestCase):
    """transfer() -> balanceUpdate() -> withdraw(), deposit(); recordTransfer()"""

    def _cfg(self, withdraw_class: str, deposit_class: str) -> dict:
        return {
            "entryPoint": "transfer",
            "nodes": [
                node("e_transfer", "entry", "transfer"),
                node("c_balanceUpdate", "call", "balanceUpdate", "transfer"),
                node("c_recordTransfer", "call", "pkg.Ledger.record", "transfer"),
                node("e_balanceUpdate", "entry", "balanceUpdate"),
                node("c_withdraw", "call", withdraw_class, "balanceUpdate"),
                node("c_deposit", "call", deposit_class, "balanceUpdate"),
                node("leaf_withdraw", "leaf", withdraw_class),
                node("leaf_deposit", "leaf", deposit_class),
                node("leaf_recordTransfer", "leaf", "pkg.Ledger.record"),
            ],
            "edges": [
                edge("e_transfer", "c_balanceUpdate"),
                edge("c_balanceUpdate", "c_recordTransfer"),
                edge("e_balanceUpdate", "c_withdraw"),
                edge("c_withdraw", "c_deposit"),
                edge("c_balanceUpdate", "e_balanceUpdate", "invoke"),
                edge("c_withdraw", "leaf_withdraw", "invoke"),
                edge("c_deposit", "leaf_deposit", "invoke"),
                edge("c_recordTransfer", "leaf_recordTransfer", "invoke"),
            ],
        }

    def test_one_phase_merges_call_site_shown_unambiguously(self):
        # withdraw/deposit share a receiver class -> balanceUpdate resolves
        # to ONE phase -> [balance update, record transfer]. Unlike the
        # explode case below, there's only one candidate phase for
        # balanceUpdate's own call site to belong to, so it IS included
        # (R6 refinement) -- excluding it here would only lose a label,
        # not avoid any ambiguity.
        flattened = flatten_intermethod_cfg(
            self._cfg("pkg.Account.withdraw", "pkg.Account.deposit")
        )
        tree = build_phase_tree(flattened)

        self.assertEqual(len(tree["phases"]), 2)
        self.assertEqual(
            set(phase_names(tree, flattened)[0]),
            {"balanceUpdate", "pkg.Account.withdraw", "pkg.Account.deposit"},
        )
        self.assertEqual(phase_names(tree, flattened)[1], ["pkg.Ledger.record"])

    def test_multiple_phases_explode_call_site_never_shown(self):
        # withdraw/deposit on unrelated classes -> balanceUpdate resolves
        # to TWO phases -> [withdraw], [deposit], [record transfer].
        flattened = flatten_intermethod_cfg(
            self._cfg("pkg.Wallet.withdraw", "pkg.Vault.deposit")
        )
        tree = build_phase_tree(flattened)

        self.assertEqual(
            phase_names(tree, flattened),
            [["pkg.Wallet.withdraw"], ["pkg.Vault.deposit"], ["pkg.Ledger.record"]],
        )
        self.assertNotIn("balanceUpdate", all_names(tree, flattened))

    def test_no_entry_nodes_ever_in_output(self):
        # R2, checked structurally rather than by name, across both cases.
        for withdraw_class, deposit_class in [
            ("pkg.Account.withdraw", "pkg.Account.deposit"),
            ("pkg.Wallet.withdraw", "pkg.Vault.deposit"),
        ]:
            flattened = flatten_intermethod_cfg(self._cfg(withdraw_class, deposit_class))
            tree = build_phase_tree(flattened)
            entry_ids = {n["id"] for n in flattened["nodes"] if n["type"] == "entry"}
            output_ids = {n for phase in tree["phases"] for n in phase["nodes"]}
            self.assertFalse(output_ids & entry_ids)


# --------------------------------------------------------------------------
# Genuine polymorphism: one call site, multiple real invoke targets
# --------------------------------------------------------------------------

class PolymorphismTests(unittest.TestCase):
    def test_polymorphic_call_site_splices_every_target(self):
        # run() calls shape.area() (Shape.area) which resolves to TWO real
        # implementations (Circle.area, Square.area), then logs.
        filtered_cfg = {
            "entryPoint": "run",
            "nodes": [
                node("e_run", "entry", "run"),
                node("c_area", "call", "Shape.area", "run"),
                node("c_after", "call", "Logger.log", "run"),
                node("e_circle", "entry", "Circle.area"),
                node("c_circle_calc", "call", "Circle.calc", "Circle.area"),
                node("e_square", "entry", "Square.area"),
                node("c_square_calc", "call", "Square.calc", "Square.area"),
                node("leaf_circle_calc", "leaf", "Circle.calc"),
                node("leaf_square_calc", "leaf", "Square.calc"),
                node("leaf_after", "leaf", "Logger.log"),
            ],
            "edges": [
                edge("e_run", "c_area"),
                edge("c_area", "c_after"),
                edge("c_area", "e_circle", "invoke"),
                edge("c_area", "e_square", "invoke"),  # 2nd real target: polymorphism
                edge("e_circle", "c_circle_calc"),
                edge("c_circle_calc", "leaf_circle_calc", "invoke"),
                edge("e_square", "c_square_calc"),
                edge("c_square_calc", "leaf_square_calc", "invoke"),
                edge("c_after", "leaf_after", "invoke"),
            ],
        }
        flattened = flatten_intermethod_cfg(filtered_cfg)
        tree = build_phase_tree(flattened)  # must not raise

        names = all_names(tree, flattened)
        self.assertIn("Circle.calc", names)
        self.assertIn("Square.calc", names)
        self.assertIn("Logger.log", names)
        # Each polymorphic target is independently resolved and spliced
        # (DESIGN.md §3 bug fix #2); Circle.area and Square.area each
        # resolve to exactly one phase on their own, so Shape.area's call
        # site is unambiguous within each and appears in both.
        self.assertIn("Shape.area", names)

        # Logger.log is reached from BOTH implementations' own tails, so it
        # is genuine Level-1 convergence (in-degree 2), regardless of which
        # implementation's return edge the walk happens to process first.
        log_phase = next(
            p
            for p in tree["phases"]
            if "Logger.log"
            in [
                n["calleeFullName"] for n in flattened["nodes"] if n["id"] in p["nodes"]
            ]
        )
        self.assertEqual(log_phase["opened_by"]["reason"], "gate")


# --------------------------------------------------------------------------
# Regression: R7's convergence check must read the node the flattened
# graph's edge actually lands on (the call site), not the callee's own
# internal content -- found by hand-tracing real project output where the
# converging point ALSO invoked further internal work, a shape the
# PolymorphismTests fixture above doesn't exercise (there, the converging
# call -- Logger.log -- is leaf-only).
# --------------------------------------------------------------------------

class ConvergenceIntoSplicedCallTests(unittest.TestCase):
    def test_two_branches_converging_on_an_internally_traversed_call_still_gate(self):
        # run(): check(); if (...) pathA(); else pathB(); wrapUp();
        # pathA/pathB are leaves; wrapUp() itself invokes further internal
        # work (wrapUpInner()). Both branches are real, live, and reconverge
        # on wrapUp() -- genuine Level-1 convergence, in-degree 2 on
        # wrapUp()'s own call site in the flattened graph.
        filtered_cfg = {
            "entryPoint": "run",
            "nodes": [
                node("e_run", "entry", "run"),
                node("c_check", "call", "Checker.check", "run"),
                node("c_pathA", "call", "Logger.pathA", "run"),
                node("c_pathB", "call", "Logger.pathB", "run"),
                node("c_wrapUp", "call", "wrapUp", "run"),
                node("e_wrapUp", "entry", "wrapUp"),
                node("c_wrapUp_inner", "call", "Logger.wrapUpInner", "wrapUp"),
                node("leaf_check", "leaf", "Checker.check"),
                node("leaf_pathA", "leaf", "Logger.pathA"),
                node("leaf_pathB", "leaf", "Logger.pathB"),
                node("leaf_wrapUp_inner", "leaf", "Logger.wrapUpInner"),
            ],
            "edges": [
                edge("e_run", "c_check"),
                edge("c_check", "c_pathA"),
                edge("c_check", "c_pathB"),
                edge("c_pathA", "c_wrapUp"),
                edge("c_pathB", "c_wrapUp"),
                edge("c_check", "leaf_check", "invoke"),
                edge("c_pathA", "leaf_pathA", "invoke"),
                edge("c_pathB", "leaf_pathB", "invoke"),
                edge("c_wrapUp", "e_wrapUp", "invoke"),
                edge("e_wrapUp", "c_wrapUp_inner"),
                edge("c_wrapUp_inner", "leaf_wrapUp_inner", "invoke"),
            ],
        }
        flattened = flatten_intermethod_cfg(filtered_cfg)
        tree = build_phase_tree(flattened)

        wrapup_phase = next(
            p for p in tree["phases"]
            if "Logger.wrapUpInner" in
            [n["calleeFullName"] for n in flattened["nodes"] if n["id"] in p["nodes"]]
        )
        self.assertEqual(wrapup_phase["opened_by"]["reason"], "gate")


# --------------------------------------------------------------------------
# Regression: a dead-end sibling of a call that itself invokes further
# internal work (and is therefore excluded from phase membership, R6) must
# still find the right phase to merge into -- found via the real project's
# BankAccountService.transfer, where the "Both accounts must exist" guard
# was wrongly landing in its own standalone phase instead of merging with
# withdraw()'s own (spliced-in) guards.
# --------------------------------------------------------------------------

class DeadEndSiblingOfSplicedCallTests(unittest.TestCase):
    def test_dead_end_sibling_merges_into_spliced_calls_own_phase(self):
        # run(): if (invalid) throw ...; else doWork();  -- doWork() itself
        # invokes further internal work (doWork() -> inner()), but its own
        # body resolves to exactly one phase (just inner()), so doWork's
        # call site IS included (R6 refinement, unambiguous).
        filtered_cfg = {
            "entryPoint": "run",
            "nodes": [
                node("e_run", "entry", "run"),
                node("c_guard", "call", "Exception.<init>", "run", dead=True),
                node("c_doWork", "call", "doWork", "run"),
                node("e_doWork", "entry", "doWork"),
                node("c_inner", "call", "Worker.inner", "doWork"),
                node("leaf_guard", "leaf", "Exception.<init>"),
                node("leaf_inner", "leaf", "Worker.inner"),
            ],
            "edges": [
                edge("e_run", "c_guard"),
                edge("e_run", "c_doWork"),
                edge("c_guard", "leaf_guard", "invoke"),
                edge("c_doWork", "e_doWork", "invoke"),
                edge("e_doWork", "c_inner"),
                edge("c_inner", "leaf_inner", "invoke"),
            ],
        }
        flattened = flatten_intermethod_cfg(filtered_cfg)
        tree = build_phase_tree(flattened)

        self.assertEqual(len(tree["phases"]), 1)
        self.assertEqual(
            set(phase_names(tree, flattened)[0]), {"Exception.<init>", "Worker.inner", "doWork"}
        )


# --------------------------------------------------------------------------
# Fully-dead body: every visible branch throws, no live path at all
# (Account.withdraw's real shape, DESIGN.md §4.1/§8.6) -- exercises the
# fallback-edge mechanism, whose source is the callee's own entry node.
# --------------------------------------------------------------------------

class FullyDeadBodyTests(unittest.TestCase):
    def test_fallback_edge_makes_continuation_reachable(self):
        # transfer() calls withdraw() then deposit(). withdraw()'s ENTIRE
        # body is two throw guards -- no visible normal-completion branch
        # at all (it has zero calls, so it's invisible to this call-
        # projected CFG, same as the real Account.withdraw). deposit()
        # must still be reachable via the synthesized fallback edge.
        filtered_cfg = {
            "entryPoint": "transfer",
            "nodes": [
                node("e_transfer", "entry", "transfer"),
                node("c_withdraw", "call", "Account.withdraw", "transfer"),
                node("c_deposit", "call", "Account.deposit", "transfer"),
                node("e_withdraw", "entry", "Account.withdraw"),
                node("c_guard1", "call", "IllegalArgumentException.<init>", "Account.withdraw", dead=True),
                node("c_guard2", "call", "InsufficientFundsException.<init>", "Account.withdraw", dead=True),
                node("leaf_guard1", "leaf", "IllegalArgumentException.<init>"),
                node("leaf_guard2", "leaf", "InsufficientFundsException.<init>"),
                node("leaf_deposit", "leaf", "Account.deposit"),
            ],
            "edges": [
                edge("e_transfer", "c_withdraw"),
                edge("c_withdraw", "c_deposit"),
                edge("c_withdraw", "e_withdraw", "invoke"),
                edge("e_withdraw", "c_guard1"),
                edge("e_withdraw", "c_guard2"),
                edge("c_guard1", "leaf_guard1", "invoke"),
                edge("c_guard2", "leaf_guard2", "invoke"),
                edge("c_deposit", "leaf_deposit", "invoke"),
            ],
        }
        flattened = flatten_intermethod_cfg(filtered_cfg)

        # Confirm the fixture actually exercises the fallback path before
        # asserting anything about phaser's handling of it.
        fallback_edges = [e for e in flattened["edges"] if e.get("fallback")]
        self.assertEqual(len(fallback_edges), 1)
        source_node = next(n for n in flattened["nodes"] if n["id"] == fallback_edges[0]["from"])
        self.assertEqual(source_node["type"], "entry")

        tree = build_phase_tree(flattened)  # must not raise (KeyError on phase_of)

        names = all_names(tree, flattened)
        self.assertIn("Account.deposit", names)
        self.assertIn("IllegalArgumentException.<init>", names)
        self.assertIn("InsufficientFundsException.<init>", names)
        # withdraw()'s own body resolves to exactly one phase (both guards
        # merge, R4's degenerate case), so its own call site is unambiguous
        # and included (R6 refinement) -- unlike the excluded-call-site
        # cases elsewhere, there's only one phase it could belong to.
        self.assertIn("Account.withdraw", names)

        guard_phase = next(
            p for p in tree["phases"]
            if "IllegalArgumentException.<init>" in
            [n["calleeFullName"] for n in flattened["nodes"] if n["id"] in p["nodes"]]
        )
        guard_phase_names = [
            n["calleeFullName"] for n in flattened["nodes"] if n["id"] in guard_phase["nodes"]
        ]
        self.assertIn("InsufficientFundsException.<init>", guard_phase_names)
        self.assertIn("Account.withdraw", guard_phase_names)

        deposit_phase = next(
            p for p in tree["phases"]
            if "Account.deposit" in
            [n["calleeFullName"] for n in flattened["nodes"] if n["id"] in p["nodes"]]
        )
        # R3: the two dead guards must not make deposit() read as gated --
        # only one LIVE successor ever reaches it (the fallback edge).
        self.assertNotEqual(deposit_phase["opened_by"]["reason"], "gate")
        # R7: attributed to the original withdraw() call site, not the
        # fallback edge's real source (withdraw's own entry node).
        withdraw_call_id = next(
            n["id"] for n in flattened["nodes"]
            if n.get("origId") == "c_withdraw"
        )
        self.assertEqual(deposit_phase["opened_by"]["subject"], withdraw_call_id)


# --------------------------------------------------------------------------
# Mutual recursion: A -> B -> A
# --------------------------------------------------------------------------

class MutualRecursionTests(unittest.TestCase):
    def test_recursive_tail_terminates_and_contributes_nothing_extra(self):
        # A: logStart(); B(); -- B: A();  (mutual, tail-call recursion).
        # Termination here actually comes from flatten_intermethod_cfg's
        # own recursion guard (a revisited method is cloned as a bare,
        # unwalked stub) -- this test confirms phaser's OWN recursive
        # resolution correctly bottoms out on that stub (empty phase list,
        # no crash) rather than assuming its own separate cycle detection.
        filtered_cfg = {
            "entryPoint": "A",
            "nodes": [
                node("e_A", "entry", "A"),
                node("c_log", "call", "Logger.logStart", "A"),
                node("c_B", "call", "B", "A"),
                node("e_B", "entry", "B"),
                node("c_A_rec", "call", "A", "B"),
                node("leaf_log", "leaf", "Logger.logStart"),
            ],
            "edges": [
                edge("e_A", "c_log"),
                edge("c_log", "c_B"),
                edge("c_log", "leaf_log", "invoke"),
                edge("c_B", "e_B", "invoke"),
                edge("e_B", "c_A_rec"),
                edge("c_A_rec", "e_A", "invoke"),  # closes the cycle
            ],
        }
        flattened = flatten_intermethod_cfg(filtered_cfg)  # must not hang/raise
        tree = build_phase_tree(flattened)  # must not hang/raise

        names = all_names(tree, flattened)
        self.assertEqual(names, {"Logger.logStart"})
        self.assertNotIn("B", names)  # R6: B's own call site is never a member
        self.assertNotIn("A", names)  # the recursive call back into A likewise


if __name__ == "__main__":
    unittest.main()
