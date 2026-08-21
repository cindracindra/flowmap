"""Compatibility façade for the stage-oriented CFG modules.

New code should import the owning stage module. Existing callers keep this
module while downstream imports migrate.
"""

from domain.cfg_filtering import filter_noise_cfg
from domain.cfg_flattening import flatten_cfg
from domain.cfg_slicing import (
    classify_roots_and_orphans,
    filter_and_classify_roots_and_orphans,
    slice_from_root,
)
from domain.opseq_orchestration import CfgPipelineResult, prepare_operation_cfg

__all__ = [
    "CfgPipelineResult",
    "classify_roots_and_orphans",
    "filter_and_classify_roots_and_orphans",
    "filter_noise_cfg",
    "flatten_cfg",
    "prepare_operation_cfg",
    "slice_from_root",
]
