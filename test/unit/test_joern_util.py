from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.src.flowmap.joern import util  # noqa: E402


class PidOnPortTests(unittest.TestCase):
    @patch("backend.src.flowmap.joern.util.subprocess.check_output")
    def test_queries_only_the_tcp_listener(self, check_output):
        check_output.return_value = "1234\n"

        self.assertEqual(util.pid_on_port(8080), 1234)
        check_output.assert_called_once_with(
            ["lsof", "-nP", "-iTCP:8080", "-sTCP:LISTEN", "-t"],
            text=True,
        )

    @patch("backend.src.flowmap.joern.util.subprocess.check_output")
    def test_returns_none_when_no_listener_exists(self, check_output):
        check_output.side_effect = subprocess.CalledProcessError(1, "lsof")

        self.assertIsNone(util.pid_on_port(8080))


if __name__ == "__main__":
    unittest.main()
