package com.flowmap.fixture;

import java.util.List;
import java.util.Objects;

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
    private String currentValue;

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
        nestedIdentifierBranches(true, false, true);
        visibleCallBeforeCallFreeNestedBranchShape(true, false);
        consecutiveIdentifierBranches(false, true);
        whileLoopShape(false);
        doWhileLoopShape(false);
        doWhileCallConditionShape();
        forLoopShape(2);
        forLoopWithUpdateCallShape(2);
        enhancedForLoopShape(List.of("loop"));
        enhancedForWithFilteredPreheaderShape(new String[] {"loop"});
        loopWithBranchShape(false, true);
        loopWithBranchThenWorkShape(false, true);
        loopWithNestedBranchTailShape(false, true, false);
        nestedLoopShape(false, false);
        consecutiveLoopShape(false, false);
        loopWithBreakShape(false, false);
        tryCatchShape(false);
        tryMultipleCatchShape(0);
        tryFinallyNestedStructureShape(false, false);
        tryReturnFinallyShape(false);
        tryThrowCatchFinallyShape(false);
        tryThrowFromCatchFinallyShape(false);
        tryFinallyOverridesReturnShape(false);
        branchThenTryInlineEntryShape(false);
        tryLoopBreakFinallyShape(false, false);
        threeArmConvergingShape(false, false);
        threeArmWithReturnShape(false, false);
        allTerminalBranchShape(false, new IllegalStateException("terminal"));
        filteredEmptyLoopShape(false);
        nestedConsecutiveEmptyGroupsShape(false, false, false);
        loopWithSwitchBreakShape(false, 0);
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

    private void shortCircuitAndConditionReturn() {
        doInner();
        if (hasRole() && isOwner()) {
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

    private void inlineArgumentOrderShape() {
        wrapperTwo(helperA(), helperB());
    }

    private void nestedInlineArgumentOrderShape() {
        wrapperTwo(wrapper(helperA()), helperB());
    }

    private void inlineOrderAfterLoopShape(boolean running) {
        while (running) {
            doX();
        }
        wrapperTwo(helperA(), helperB());
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

    private int wrapperTwo(int left, int right) {
        return left + right;
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

    private void nestedIdentifierBranches(
            boolean first, boolean second, boolean nested) {
        if (first) {
            if (nested) {
                doX();
            }
        } else if (second) {
            if (nested) {
                doInner();
            }
        } else {
            return;
        }
        doHelper();
    }

    private void visibleCallBeforeCallFreeNestedBranchShape(
            boolean outer, boolean inner) {
        if (outer) {
            doX();
            if (inner) {
                doInner();
            }
        }
        doHelper();
    }

    private void whileLoopShape(boolean repeat) {
        while (repeat) {
            doInner();
        }
        doX();
    }

    private void doWhileLoopShape(boolean repeat) {
        do {
            doInner();
        } while (repeat);
        doX();
    }

    private void doWhileCallConditionShape() {
        do {
            doInner();
        } while (hasRole());
        doX();
    }

    private void forLoopShape(int count) {
        for (int index = 0; index < count; index++) {
            doInner();
        }
        doX();
    }

    private void forLoopWithUpdateCallShape(int count) {
        for (int index = 0; index < count; index = advance(index)) {
            doInner();
        }
        doX();
    }

    private int advance(int value) {
        return value + 1;
    }

    private void enhancedForLoopShape(List<String> values) {
        for (String value : values) {
            doInner();
        }
        doX();
    }

    private void enhancedForWithFilteredPreheaderShape(String... values) {
        Objects.requireNonNull(values);
        for (String value : values) {
            currentValue = value.trim();
        }
        doX();
    }

    private void loopWithBranchShape(boolean repeat, boolean choose) {
        while (repeat) {
            if (choose) {
                doInner();
            } else {
                doHelper();
            }
        }
        doX();
    }

    private void loopWithBranchThenWorkShape(boolean repeat, boolean choose) {
        while (repeat) {
            if (choose) {
                doInner();
            } else {
                doHelper();
            }
            doY();
        }
        doX();
    }

    private void loopWithNestedBranchTailShape(
            boolean repeat, boolean outer, boolean inner) {
        while (repeat) {
            if (outer) {
                if (inner) {
                    doInner();
                } else {
                    doHelper();
                }
            } else {
                doY();
            }
        }
        doX();
    }

    private void nestedLoopShape(boolean outer, boolean inner) {
        while (outer) {
            while (inner) {
                doInner();
            }
            doY();
        }
        doX();
    }

    private void consecutiveLoopShape(boolean first, boolean second) {
        while (first) {
            doInner();
        }
        while (second) {
            doY();
        }
        doX();
    }

    private void loopWithBreakShape(boolean repeat, boolean stop) {
        while (repeat) {
            if (stop) {
                break;
            }
            doInner();
        }
        doX();
    }

    private void tryCatchShape(boolean fail) {
        try {
            doInner();
            if (fail) {
                throw new IllegalArgumentException("try failure");
            }
            doHelper();
        } catch (IllegalArgumentException failure) {
            doY();
        }
        doX();
    }

    private void tryMultipleCatchShape(int mode) {
        try {
            doInner();
            if (mode == 1) {
                throw new IllegalArgumentException("first");
            }
            if (mode == 2) {
                throw new IllegalStateException("second");
            }
            doHelper();
        } catch (IllegalArgumentException failure) {
            doX();
        } catch (IllegalStateException failure) {
            doY();
        }
        doInner();
    }

    private void tryFinallyNestedStructureShape(boolean choose, boolean repeat) {
        try {
            if (choose) {
                while (repeat) {
                    doInner();
                }
            } else {
                doHelper();
            }
            doY();
        } catch (IllegalArgumentException failure) {
            if (choose) {
                doX();
            }
        } finally {
            doInner();
        }
        doHelper();
    }

    private void tryReturnFinallyShape(boolean done) {
        try {
            doInner();
            if (done) {
                return;
            }
            doHelper();
        } finally {
            doX();
        }
        doY();
    }

    private void tryThrowCatchFinallyShape(boolean fail) {
        try {
            if (fail) {
                throw new IllegalArgumentException("caught");
            }
            doInner();
        } catch (IllegalArgumentException failure) {
            doHelper();
        } finally {
            doX();
        }
        doY();
    }

    private void tryThrowFromCatchFinallyShape(boolean fail) {
        try {
            doInner();
            if (fail) {
                throw new IllegalArgumentException("caught");
            }
        } catch (IllegalArgumentException failure) {
            throw new IllegalStateException("replacement");
        } finally {
            doX();
        }
        doHelper();
    }

    private void tryFinallyOverridesReturnShape(boolean done) {
        try {
            if (done) {
                return;
            }
            doInner();
        } finally {
            if (done) {
                throw new IllegalStateException("override");
            }
            doX();
        }
        doHelper();
    }

    private void branchThenTryInlineEntryShape(boolean stop) {
        if (stop) {
            return;
        }
        try {
            wrapperTwo(helperA(), helperB());
        } catch (RuntimeException error) {
            doX();
        }
        doY();
    }

    private void tryLoopBreakFinallyShape(boolean stop, boolean cleanup) {
        try {
            while (cleanup) {
                if (stop) {
                    break;
                }
                doX();
            }
        } finally {
            if (cleanup) {
                doY();
            }
        }
        doHelper();
    }

    private void threeArmConvergingShape(boolean first, boolean second) {
        if (first) {
            doX();
        } else if (second) {
            doInner();
        } else {
            doHelper();
        }
        doY();
    }

    private void threeArmWithReturnShape(boolean first, boolean second) {
        if (first) {
            doX();
        } else if (second) {
            doInner();
        } else {
            return;
        }
        doHelper();
    }

    private void allTerminalBranchShape(
            boolean chooseReturn, RuntimeException failure) {
        if (chooseReturn) {
            return;
        } else {
            throw failure;
        }
    }

    private void filteredEmptyLoopShape(boolean repeat) {
        while (repeat) {
            int ignored = 1;
            ignored++;
        }
        doX();
    }

    private void nestedConsecutiveEmptyGroupsShape(
            boolean outer, boolean inner, boolean later) {
        if (outer) {
            if (inner) {
                int ignored = 1;
                ignored++;
            }
        }
        if (later) {
            int ignored = 2;
            ignored++;
        }
        doX();
    }

    private void loopWithContinueShape(boolean repeat, boolean skip) {
        while (repeat) {
            if (skip) {
                continue;
            }
            doInner();
        }
        doX();
    }

    private void nestedLoopTransferShape(
            boolean outer, boolean inner, boolean stop, boolean skip) {
        while (outer) {
            while (inner) {
                if (stop) {
                    break;
                }
                if (skip) {
                    continue;
                }
                doInner();
            }
            doY();
        }
        doX();
    }

    private void loopWithSwitchBreakShape(boolean repeat, int mode) {
        while (repeat) {
            switch (mode) {
                case 0:
                    doInner();
                    break;
                default:
                    doHelper();
            }
            doY();
        }
        doX();
    }

    private void branchThenLoopShape(boolean choose, boolean repeat) {
        if (choose) {
            doInner();
        } else {
            doHelper();
        }
        while (repeat) {
            doY();
        }
        doX();
    }

    private void loopThenBranchShape(boolean repeat, boolean choose) {
        while (repeat) {
            doInner();
        }
        if (choose) {
            doY();
        } else {
            doHelper();
        }
        doX();
    }

    private void branchContainingLoopShape(boolean choose, boolean repeat) {
        if (choose) {
            while (repeat) {
                doInner();
            }
        } else {
            doHelper();
        }
        doX();
    }

    private void consecutiveIdentifierBranches(boolean first, boolean second) {
        if (first) {
            doX();
        }
        if (second) {
            doInner();
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
