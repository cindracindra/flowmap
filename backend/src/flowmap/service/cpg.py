from __future__ import annotations
import subprocess

from joern.util import find_joern_parse


def parse_project(source_dir: str, cpg_out: str) -> None:
    """
    Parse a Java source directory into a CPG file.

    Runs once per initial load or explicit user-triggered refresh.
    The process exits on completion; no state is retained afterward.
    """
    joern_parse = find_joern_parse()
    subprocess.run([joern_parse, source_dir, "-o", cpg_out], check=True)


