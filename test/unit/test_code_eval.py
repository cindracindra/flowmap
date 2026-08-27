from __future__ import annotations

import json
import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"))

from model import Edge, Graph, Node  # noqa: E402
from data.code_eval import EvaluationRecorder, collect_codebase_stats, collect_graph_stats


class StatsTests(unittest.TestCase):
    def test_collects_java_and_graph_load_units(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "A.java").write_text(
                "// heading\nclass A {\n  void run() {}\n}\n", encoding="utf-8"
            )
            source = collect_codebase_stats(root)
        self.assertEqual(source.java_files, 1)
        self.assertEqual(source.declared_types, 1)
        self.assertEqual(source.source_lines, 3)

        graph = Graph(
            nodes=[Node("e", "entry"), Node("c", "call")],
            edges=[Edge(source="e", target="c", type="sequence")],
            roots=["e"],
        )
        stats = collect_graph_stats(graph)
        self.assertEqual((stats.nodes, stats.edges, stats.roots), (2, 1, 1))

    def test_recorder_writes_stage_and_llm_records(self):
        recorder = EvaluationRecorder(run_id="test")
        outputs = {}
        with recorder.stage("filter", input_stats={"nodes": 2}, output_stats=outputs):
            outputs["nodes"] = 1
        recorder.record_llm_call({
            "call_site": "label", "provider": "groq", "model": "m", "role": "small",
            "duration_seconds": 0.1, "success": True, "prompt_characters": 10,
            "response_characters": 2,
        })
        with tempfile.TemporaryDirectory() as directory:
            output = recorder.write_json(Path(directory) / "run.json")
            payload = json.loads(output.read_text())
        self.assertEqual(payload["stages"][0]["output_stats"]["nodes"], 1)
        self.assertEqual(payload["llm_calls"][0]["call_site"], "label")


if __name__ == "__main__":
    unittest.main()
