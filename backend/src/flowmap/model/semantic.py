from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


OperationRole = Literal[
    "purposeful",
    "atomic",
    "expanded-container",
    "exception-mechanic",
    "structural",
]


@dataclass(slots=True)
class NodeSemanticFeatures:
    """Semantic evidence for one existing CFG node.
    """

    # Object the method is called on, e.g. account in account.withdraw().
    receiver: str | None = None
    receiverType: str | None = None

    # Expressions passed into the call.
    arguments: list[str] = field(default_factory=list)
    argumentTypes: list[str] = field(default_factory=list)

    # Fields used as inputs by the call, including identifiers inside
    # argument expressions, e.g. customer and accountId in customer.accountId.
    inputIdentifiers: list[str] = field(default_factory=list)

    # Object or class fields whose values are read by the call or its callee.
    fieldsRead: list[str] = field(default_factory=list)

    # Object or class fields modified by the call or its callee.
    fieldsWritten: list[str] = field(default_factory=list)

    # Method’s return type.
    outputType: str | None = None

    # Application-specific types associated with the operation, gathered
    # from the receiver, arguments, return type, or referenced fields.
    domainTypes: list[str] = field(default_factory=list)

    # Words extracted from the method name.
    methodTerms: list[str] = field(default_factory=list)

    # Categories/ Fields the extractor successfully checked.
    observedFeatures: list[str] = field(default_factory=list)

    role: OperationRole | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NodeSemanticFeatures:
        return cls(
            receiver=data.get("receiver"),
            receiverType=data.get("receiverType"),
            arguments=list(data.get("arguments", [])),
            argumentTypes=list(data.get("argumentTypes", [])),
            inputIdentifiers=list(data.get("inputIdentifiers", [])),
            fieldsRead=list(data.get("fieldsRead", [])),
            fieldsWritten=list(data.get("fieldsWritten", [])),
            outputType=data.get("outputType"),
            domainTypes=list(data.get("domainTypes", [])),
            methodTerms=list(data.get("methodTerms", [])),
            observedFeatures=list(data.get("observedFeatures", [])),
            role=data.get("role"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in (
            ("receiver", self.receiver),
            ("receiverType", self.receiverType),
            ("outputType", self.outputType),
            ("role", self.role),
        ):
            if value is not None:
                result[name] = value
        for name, values in (
            ("arguments", self.arguments),
            ("argumentTypes", self.argumentTypes),
            ("inputIdentifiers", self.inputIdentifiers),
            ("fieldsRead", self.fieldsRead),
            ("fieldsWritten", self.fieldsWritten),
            ("domainTypes", self.domainTypes),
            ("methodTerms", self.methodTerms),
            ("observedFeatures", self.observedFeatures),
        ):
            if values:
                result[name] = list(values)
        return result
