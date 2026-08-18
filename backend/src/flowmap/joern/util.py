from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


# --------------------------------------------------------------------------
# Binary discovery
# --------------------------------------------------------------------------


def find_joern_parse() -> str:
    """Locate the joern-parse binary; raise if not found."""
    joern_parse = shutil.which("joern-parse")
    if joern_parse:
        return joern_parse

    candidates = [
        Path.home() / ".joern" / "joern-cli" / "joern-parse",
        Path.home() / "bin" / "joern" / "joern-cli" / "joern-parse",
        Path("/opt/joern/joern-cli/joern-parse"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise FileNotFoundError(
        "joern-parse not found on PATH or in known install locations."
    )


def find_joern() -> str:
    """Locate the joern binary; raise if not found."""
    joern = shutil.which("joern")
    if joern:
        return joern

    candidates = [
        Path.home() / ".joern" / "joern-cli" / "joern",
        Path.home() / "bin" / "joern" / "joern-cli" / "joern",
        Path("/opt/joern/joern-cli/joern"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise FileNotFoundError(
        "joern not found on PATH. Install with:\n"
        "  curl -L https://github.com/joernio/joern/releases/latest/download/joern-install.sh | bash\n"
        '  export PATH="$HOME/.joern/joern-cli:$PATH"'
    )


# --------------------------------------------------------------------------
# Port/process helpers
# --------------------------------------------------------------------------


def pid_on_port(port: int) -> int | None:
    """Return the PID of the process listening on a TCP port.

    Restricting ``lsof`` to listeners is important once a Joern client has
    connected: a broad ``lsof -i :PORT`` also returns the Python client.  If
    that client PID is mistaken for the server PID, ``JoernSession.stop()``
    sends SIGTERM to its own Python process and leaves the JVM orphaned.
    """
    try:
        output = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            text=True,
        ).strip()
        if not output:
            return None
        return int(output.splitlines()[0])
    except subprocess.CalledProcessError:
        return None


def is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # signal 0: existence check only, sends nothing
        return True
    except (ProcessLookupError, PermissionError):
        return False
