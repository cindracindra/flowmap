from model import Graph


def has_operation_body(graph: Graph) -> bool:
    """Return whether a filtered opseq contains executable body content.

    Entry and exit nodes are structural shells. Once noise filtering removes
    every call node, exporting that shell as an operation only creates an
    empty picker item with nothing to visualise or classify.
    """
    return any(node.type == "call" for node in graph.nodes)
