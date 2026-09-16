from evaluation.build_structural_entry_fixtures import match_entries


def test_trace_entry_matches_flowmap_signature() -> None:
    graph = {
        "nodes": [
            {
                "id": "entry-a",
                "type": "entry",
                "calleeFullName": "example.Service.run:void(java.lang.String,int)",
            }
        ]
    }
    traced = [
        {
            "entry_class": "example.Service",
            "entry_method": "run",
            "descriptor": "(Ljava/lang/String;I)V",
        }
    ]

    selectors, rows = match_entries(traced, graph)

    assert selectors == ["example.Service.run:void(java.lang.String,int)"]
    assert rows[0]["status"] == "resolved"


def test_trace_entry_reports_unresolved_method() -> None:
    selectors, rows = match_entries(
        [
            {
                "entry_class": "example.Missing",
                "entry_method": "run",
                "descriptor": "()V",
            }
        ],
        {"nodes": []},
    )

    assert selectors == []
    assert rows[0]["status"] == "unresolved"
