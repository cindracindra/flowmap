"""Semantic signatures used by execution-phase cohesion calculations."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import ceil
import re
from typing import Iterable, Mapping

from model import NodeSemanticFeatures


FeatureName = str

_SPACE = re.compile(r"\s+")
_QUOTED_LITERAL = re.compile(r"^(?:\".*\"|'.*'|[-+]?\d+(?:\.\d+)?)$")
_PLACEHOLDER_VALUES = frozenset({"<empty>", "<unknown>"})
_CONTEXT_RECEIVERS = frozenset({"this", "super"})
_SYNTHETIC_PREFIXES = ("$",)

# Names emitted by the existing semantic extractor, mapped to the dimensions
# used by this package. Keeping the translation here gives every cohesion
# comparison the same definition of "observed".
_OBSERVATION_ALIASES: Mapping[str, frozenset[FeatureName]] = {
    "receiver": frozenset({"receivers"}),
    "inputs": frozenset({"inputs"}),
    "arguments": frozenset({"arguments"}),
    # The Joern extractor distinguishes call-site field inspection from
    # callee-body inspection.  Both can observe reads, while writes can only
    # be established when an internal callee body is available.
    "callsiteFields": frozenset({"fields_read"}),
    "calleeFields": frozenset({"fields_read", "fields_written"}),
    "fieldsRead": frozenset({"fields_read"}),
    "fieldsWritten": frozenset({"fields_written"}),
    "domainTypes": frozenset({"domain_types"}),
    "methodTerms": frozenset({"method_terms"}),
    "output": frozenset({"output_types"}),
    "outputType": frozenset({"output_types"}),
}


def _normalise(value: str) -> str:
    return _SPACE.sub("", value).strip().lower()


def _values(values: Iterable[str]) -> frozenset[str]:
    normalised = (_normalise(value) for value in values if value)
    return frozenset(
        value
        for value in normalised
        if value
        and value not in _PLACEHOLDER_VALUES
        and not value.startswith(_SYNTHETIC_PREFIXES)
    )


def _arguments(values: Iterable[str]) -> frozenset[str]:
    return frozenset(value for value in _values(values) if not _QUOTED_LITERAL.match(value))


def _observed(values: Iterable[str]) -> frozenset[FeatureName]:
    result: set[FeatureName] = set()
    for value in values:
        result.update(_OBSERVATION_ALIASES.get(value, ()))
    return frozenset(result)


@dataclass(frozen=True, slots=True)
class SemanticSignature:
    """Normalised semantic footprint of an operation or operation region."""

    receivers: frozenset[str] = frozenset()
    inputs: frozenset[str] = frozenset()
    arguments: frozenset[str] = frozenset()
    fields_read: frozenset[str] = frozenset()
    fields_written: frozenset[str] = frozenset()
    domain_types: frozenset[str] = frozenset()
    method_terms: frozenset[str] = frozenset()
    output_types: frozenset[str] = frozenset()
    observed_features: frozenset[FeatureName] = frozenset()
    population_size: int = 1

    @classmethod
    def from_operation(cls, features: NodeSemanticFeatures) -> SemanticSignature:
        """Create a singleton signature from extracted operation features."""
        receiver = _values((features.receiver,)) if features.receiver else frozenset()
        output = _values((features.outputType,)) if features.outputType else frozenset()
        values = {
            "receivers": receiver - _CONTEXT_RECEIVERS,
            "inputs": _values(features.inputIdentifiers) - _CONTEXT_RECEIVERS,
            "arguments": _arguments(features.arguments),
            "fields_read": _values(features.fieldsRead),
            "fields_written": _values(features.fieldsWritten),
            "domain_types": _values(features.domainTypes),
            "method_terms": _values(features.methodTerms),
            "output_types": output,
        }
        return cls(
            **values,
            # This records complete extraction, not merely the presence of a
            # partially recovered value. Partial values remain usable as
            # positive evidence in the cohesion scorer.
            observed_features=_observed(features.observedFeatures),
        )

    def dimensions(self) -> dict[FeatureName, frozenset[str]]:
        return {
            "inputs": self.inputs,
            "arguments": self.arguments,
            "receivers": self.receivers,
            "fields_read": self.fields_read,
            "fields_written": self.fields_written,
            "domain_types": self.domain_types,
            "method_terms": self.method_terms,
            "output_types": self.output_types,
        }


def _core_threshold(population_size: int) -> int:
    return 1 if population_size == 1 else max(2, ceil(population_size / 2))


def build_core_signature(
    signatures: Iterable[SemanticSignature],
) -> SemanticSignature:
    """Reduce operation signatures to values present in a region's core.

    A value is core when it occurs in at least half of the operations and in
    at least two operations. A singleton retains its complete signature. An
    observation category is retained under the same rule, so a partially
    extracted region is not presented as completely observed.
    """
    items = tuple(signatures)
    if not items:
        return SemanticSignature(population_size=0)

    population_size = len(items)
    threshold = _core_threshold(population_size)
    dimensions = tuple(items[0].dimensions())

    def core(name: FeatureName) -> frozenset[str]:
        counts: Counter[str] = Counter()
        for item in items:
            counts.update(item.dimensions()[name])
        return frozenset(value for value, count in counts.items() if count >= threshold)

    observed_counts: Counter[FeatureName] = Counter()
    for item in items:
        observed_counts.update(item.observed_features)

    values = {name: core(name) for name in dimensions}
    return SemanticSignature(
        receivers=values["receivers"],
        inputs=values["inputs"],
        arguments=values["arguments"],
        fields_read=values["fields_read"],
        fields_written=values["fields_written"],
        domain_types=values["domain_types"],
        method_terms=values["method_terms"],
        output_types=values["output_types"],
        observed_features=frozenset(
            name for name, count in observed_counts.items() if count >= threshold
        ),
        population_size=population_size,
    )
