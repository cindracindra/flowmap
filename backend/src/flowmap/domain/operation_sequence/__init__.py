"""Public API for operation-sequence discovery, assignment, and labelling."""

from .assignment import OperationTopicClassifier, assign_operation_topics
from .discovery import has_operation_body
from .documents import (
    build_method_embedding_document,
    build_operation_document,
    compact_method_name,
    operation_label_method_context,
    operation_method_names,
    operation_method_sequence,
)
from .orchestration import (
    OperationLabeler,
    assign_operation_sequences_to_topics,
    discover_operation_sequences,
    label_operation_sequences,
)
from .payloads import (
    operation_assignment_payload,
    operation_label_payload,
    topic_context_payload,
)

__all__ = [
    "OperationLabeler",
    "OperationTopicClassifier",
    "assign_operation_sequences_to_topics",
    "assign_operation_topics",
    "build_operation_document",
    "discover_operation_sequences",
    "has_operation_body",
    "label_operation_sequences",
    "operation_assignment_payload",
    "operation_label_payload",
    "build_method_embedding_document",
    "compact_method_name",
    "operation_label_method_context",
    "operation_method_names",
    "operation_method_sequence",
    "topic_context_payload",
]
