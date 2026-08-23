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
        List.of("lambda").forEach(value -> doInner());
    }

    private void earlyReturn(boolean authenticated) {
        doInner();
        if (!authenticated) {
            return;
        }
        doX();
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
