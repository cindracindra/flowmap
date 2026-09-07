import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"))

from backend.src.flowmap.domain.operation_sequence import (
    build_method_embedding_document,
    build_operation_document,
    compact_method_name,
    operation_assignment_payload,
    operation_label_payload,
    topic_context_payload,
)
from backend.src.flowmap.model import Graph, MethodDocument, Node, TopicCluster


def _operation(root: str, *methods: str) -> Graph:
    return Graph(
        entryPoint=root,
        nodes=[
            Node(id=str(index), type="entry", calleeFullName=method)
            for index, method in enumerate(methods)
        ],
    )


def test_topic_context_has_one_shared_assignment_and_labelling_shape() -> None:
    cluster = TopicCluster(
        label=4,
        llm_label="Order Management",
        statistical_terms=["order", "checkout"],
        member_full_names=["shop.OrderService"],
    )

    assert topic_context_payload(cluster) == {
        "id": 4,
        "label": "Order Management",
        "representativeTerms": ["order", "checkout"],
        "memberClasses": ["shop.OrderService"],
    }


def test_method_embedding_matches_class_normalisation_and_deduplication() -> None:
    document = MethodDocument(
        methodName="getCustomerAccount",
        fullName="shop.Account.getCustomerAccount:void()",
        identifiers=["customerAccount", "customer_account", "accountID"],
        comments=["  Find   customer  ", "Find customer"],
        literals=["Account found", "Account found"],
    )

    assert build_method_embedding_document(document) == (
        "Method: get customer account\n"
        "Identifiers: customer account; account\n"
        "Comments: Find customer\n"
        "Messages: Account found"
    )


def test_operation_embedding_deduplicates_categories_across_methods() -> None:
    operation = _operation("shop.First.run", "shop.First.run", "shop.Second.run")
    documents = [
        MethodDocument(
            "getCustomerAccount",
            "shop.First.run",
            ["customerAccount"],
            ["Find customer"],
            ["Account found"],
        ),
        MethodDocument(
            "get_customer_account",
            "shop.Second.run",
            ["customer_account"],
            ["Find customer"],
            ["Account found"],
        ),
    ]

    assert build_operation_document(operation, documents) == (
        "Methods: get customer account\n"
        "Identifiers: customer account\n"
        "Comments: Find customer\n"
        "Messages: Account found"
    )

def test_assignment_payload_separates_entry_point_and_subsequent_methods() -> None:
    cluster = TopicCluster(
        label=4,
        llm_label="Order Management",
        member_full_names=["shop.OrderService"],
        fallback=True,
    )
    operation = _operation(
        "shop.Order.checkout",
        "shop.Order.checkout",
        "shop.Payment.authorise",
    )

    payload = operation_assignment_payload(["operation"], {"operation": operation}, [cluster])

    assert payload == {
        "topics": [topic_context_payload(cluster)],
        "operations": [{
            "id": "operation",
            "entryPoint": "Order.checkout",
            "subsequentMethods": ["Payment.authorise"],
        }],
    }


def test_compact_method_name_removes_package_and_signature() -> None:
    assert (
        compact_method_name("src.main.com.shop.Order.checkout:void(java.lang.String)")
        == "Order.checkout"
    )


def test_method_sequence_collapses_overload_signatures() -> None:
    operation = _operation(
        "shop.Customer.findUser:User(long)",
        "shop.Customer.findUser:User(long)",
        "shop.Customer.findUser:User(java.lang.String)",
    )

    assert operation_assignment_payload(["operation"], {"operation": operation}, []) == {
        "topics": [],
        "operations": [{
            "id": "operation",
            "entryPoint": "Customer.findUser",
            "subsequentMethods": [],
        }],
    }


def test_label_payload_contains_only_topic_reserved_labels_and_operations() -> None:
    cluster = TopicCluster(label=4, llm_label="Order Management")
    first = _operation("shop.First.run", "shop.Shared.validate")
    second = _operation("shop.Second.run", "shop.Shared.validate")

    payload = operation_label_payload(
        ["first", "second"],
        {"first": first, "second": second},
        cluster,
        {"existing": "Existing Label"},
    )

    assert payload == {
        "topic": topic_context_payload(cluster),
        "reservedLabels": {"existing": "Existing Label"},
        "operations": [
            {
                "id": "first",
                "entryPoint": "First.run",
                "subsequentMethods": ["Shared.validate"],
            },
            {
                "id": "second",
                "entryPoint": "Second.run",
                "subsequentMethods": ["Shared.validate"],
            },
        ],
    }
