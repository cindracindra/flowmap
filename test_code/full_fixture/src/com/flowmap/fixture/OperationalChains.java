package com.flowmap.fixture;

import java.util.List;

/**
 * Fixture project for processor/inter_cfg_full.sc -- method names mirror
 * test/test_full_cfg.py's hand-built raw-dump fixture 1:1 (doA,
 * doProcessTwo, doHelper, doX, doY, doInner, unusedMethod), so running
 * the real pipeline against this source and diffing the result by NAME
 * (calleeFullName), not by CPG node id, tells you whether the live
 * classify_roots_and_orphans output matches what the hand-built test
 * already asserts.
 *
 * doA() and doProcessTwo() are two independent, unrelated call chains
 * that both call doHelper() -- deliberately never invoked from anywhere
 * else in this project, so the CPG-visible call graph has no caller for
 * either: both should classify as roots, same as main() below (a THIRD
 * root this project adds beyond the Python fixture's two -- expected,
 * not a mismatch).
 *
 * unusedMethod() is a true orphan: nothing calls it, it calls nothing.
 */
public class OperationalChains {

    public void doA() {
        doHelper();
        doX();
        earlyReturn(true);
        callConditionReturn();
        shortCircuitCallConditionReturn();
        asymmetricBranch(true);
        returnHelper();
        returnConditional(true);
        returnWrapper();
        helperThenReturn();
        branchWithCalls(true);
        emptyContinuingArm(true);
        emptyReturnArm(true);
        emptyThrowArm(true, null);
        nestedEmptyBranch(true, true);
        branchAtMethodEnd(true);
        consecutiveThrowGuards(false, false);
        unequalDistanceBranch(false);
        normalPathAfterThrowGuard(false);
        consecutiveEmptyTerminalBranches(false, false, new RuntimeException("failure"));
        List.of("lambda").forEach(value -> doInner());
    }

    private void earlyReturn(boolean authenticated) {
        doInner();
        if (!authenticated) {
            return;
        }
        doX();
    }

    private void callConditionReturn() {
        doInner();
        if (hasRole()) {
            return;
        }
        doX();
    }

    private void shortCircuitCallConditionReturn() {
        doInner();
        if (hasRole() || isOwner()) {
            return;
        }
        doX();
    }

    private boolean hasRole() {
        return true;
    }

    private boolean isOwner() {
        return true;
    }

    private void asymmetricBranch(boolean existing) {
        if (existing) {
            doX();
        } else {
            String value = String.valueOf(existing);
            value.trim();
            doY();
        }
    }

    private int returnHelper() {
        return helper();
    }

    private int returnConditional(boolean condition) {
        return condition ? helperA() : helperB();
    }

    private int returnWrapper() {
        return wrapper(helper());
    }

    private void helperThenReturn() {
        helper();
        return;
    }

    private int helper() {
        return 1;
    }

    private int helperA() {
        return 2;
    }

    private int helperB() {
        return 3;
    }

    private int wrapper(int value) {
        return value;
    }

    private void branchWithCalls(boolean condition) {
        if (condition) {
            doX();
        } else {
            doInner();
        }
        doHelper();
    }

    private void emptyContinuingArm(boolean condition) {
        if (condition) {
            doX();
        }
        doHelper();
    }

    private void emptyReturnArm(boolean condition) {
        doInner();
        if (condition) {
            return;
        }
        doHelper();
    }

    private void emptyThrowArm(boolean condition, RuntimeException failure) {
        doInner();
        if (condition) {
            throw failure;
        }
        doHelper();
    }

    private void nestedEmptyBranch(boolean outer, boolean inner) {
        if (outer) {
            if (inner) {
                return;
            }
            doX();
        }
        doHelper();
    }

    private void branchAtMethodEnd(boolean condition) {
        if (condition) {
            doX();
        } else {
            doInner();
        }
    }

    private void consecutiveThrowGuards(boolean first, boolean second) {
        if (first) {
            throw new IllegalArgumentException("first guard");
        }
        if (second) {
            throw new IllegalStateException("second guard");
        }
        doHelper();
    }

    private void unequalDistanceBranch(boolean condition) {
        if (condition) {
            doX();
        } else {
            int value = 1;
            value += 2;
            doY();
        }
        doHelper();
    }

    private void normalPathAfterThrowGuard(boolean rejected) {
        if (rejected) {
            throw new IllegalArgumentException("rejected");
        }
        int value = 1;
        value += 2;
        doHelper();
    }

    private void consecutiveEmptyTerminalBranches(
            boolean first,
            boolean second,
            RuntimeException failure) {
        if (first) {
            throw failure;
        }
        if (second) {
            return;
        }
        doHelper();
    }

    public void doProcessTwo() {
        doHelper();
        doY();
    }

    void doHelper() {
        doInner();
    }

    void doInner() {
        System.out.println("doInner");
    }

    void doX() {
        System.out.println("doX");
    }

    void doY() {
        throw new IllegalArgumentException("doY");
    }

    void unusedMethod() {
        // Deliberately empty and deliberately never called -- the orphan
        // case: invoke in-degree 0, invoke out-degree 0.
    }

    public static void main(String[] args) {
        // Deliberately does NOT call doA()/doProcessTwo() -- see class
        // comment. This makes main() itself a third root.
        System.out.println("Analyze this with joern-parse, not `java`. "
            + "See test/test_full_cfg.py for the expected roots/orphans shape.");
    }
}
