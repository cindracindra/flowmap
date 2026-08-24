from dataclasses import dataclass

from domain.cfg_filtering import filter_noise_cfg
from domain.cfg_flattening import flatten_cfg
from domain.cfg_slicing import slice_from_root
from model import Graph


@dataclass(frozen=True, slots=True)
class CfgPipelineResult:
    source: Graph
    sliced: Graph
    filtered: Graph
    flattened: Graph


def has_operation_body(graph: Graph) -> bool:
    """Return whether a filtered opseq contains executable body content.

    Entry and exit nodes are structural shells. Once noise filtering removes
    every call node, exporting that shell as an operation only creates an
    empty picker item with nothing to visualise or classify.
    """
    return any(node.type == "call" for node in graph.nodes)


def prepare_operation_cfg(
    full_graph: Graph,
    root_id: str,
) -> CfgPipelineResult:
    """Slice, filter, and flatten one operation with auditable outputs."""
    sliced = slice_from_root(full_graph, root_id)
    filtered = filter_noise_cfg(sliced)
    flattened = flatten_cfg(filtered)
    return CfgPipelineResult(
        source=full_graph,
        sliced=sliced,
        filtered=filtered,
        flattened=flattened,
    )
