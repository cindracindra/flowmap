"""Stage 9: name final phases without changing their membership."""

from __future__ import annotations

from typing import Callable

from model import Graph, Phase


PhaseLabeler = Callable[[Graph, tuple[str, ...], int], str | None]


def label_phases(
    flattened: Graph,
    phases: list[Phase],
    labeler: PhaseLabeler,
) -> int:
    """Label each already-overlaid phase and return the number named.

    The labeler receives flattened clone ids, so the LLM sees the exact
    operation instance exported to the frontend. A failed or blank response
    leaves that phase unnamed. Nodes, phase order, ids and transitions are not
    modified.
    """
    labelled = 0
    for index, phase in enumerate(phases):
        label = labeler(flattened, tuple(phase.nodes), index)
        cleaned = label.strip() if label and label.strip() else None
        phase.label = cleaned
        if cleaned is not None:
            labelled += 1
    return labelled
