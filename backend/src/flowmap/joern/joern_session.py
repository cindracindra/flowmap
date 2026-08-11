from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import tempfile
import time

from joern.util import find_joern, pid_on_port, is_process_alive

# cpgqls_client only exports import_code_query (parse raw source/jar via the
# server) and workspace_query — there is no helper for loading an existing
# cpg.bin. That query is sent as a raw CPGQL string instead.
from cpgqls_client import CPGQLSClient

# The server is a remote Scala REPL: raw stdout is always the REPL's
# pretty-printed echo of the result (e.g. `val res3: List[String] = List(...)`,
# ANSI colour codes included), never the value on its own. Appending
# `.toJsonPretty` to a query makes the *value* a JSON string, which the REPL
# then echoes as `val resN: String = """...json...""". This regex pulls that
# triple-quoted payload back out so it can be parsed as real JSON.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_JSON_PAYLOAD_RE = re.compile(r'"""(.*)"""', re.DOTALL)

# Whatever json.loads() can produce -- query_json/query_script_json don't
# know ahead of time whether a given query's JSON payload is an object, an
# array, or a bare scalar, so this is the honest shape. Callers that know
# their specific query always yields one shape (e.g. get_all_methods always
# gets a JSON array) narrow it in their own return type instead.
type JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


# --------------------------------------------------------------------------
# Query phase — persistent server, one instance per active session
# --------------------------------------------------------------------------


class JoernSession:
    """
    Manages one long-lived `joern --server` process and provides query
    access against an already-loaded CPG.

    Typical usage:
        session = JoernSession(port=8080)
        session.start()
        session.load_cpg("cpg.bin")
        result = session.query("cpg.method.name.l")
        ...
        session.refresh_cpg("cpg.bin")
        ...
        session.stop()

    Or as a context manager, which guarantees stop() runs even if an
    exception is raised in between (it cannot protect against the
    process itself being killed from outside, e.g. SIGKILL or an
    external timeout -- nothing at the code level can):
        with JoernSession(port=8080) as session:
            session.load_cpg("cpg.bin")
            result = session.query("cpg.method.name.l")
    """

    def __init__(self, port: int = 8080, startup_timeout: float = 30.0):
        self.port = port
        self.startup_timeout = startup_timeout
        self._pid: int | None = None
        self._client: CPGQLSClient | None = None
        self._log_path: str | None = None
        self._launcher: subprocess.Popen | None = None
        self._prev_sigterm_handler = None
        self._prev_sigint_handler = None

    def __enter__(self) -> JoernSession:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    @property
    def log_path(self) -> str | None:
        """Path to this session's server log file, once start() has been
        called -- None before then. Not cleared by stop(), so it's still
        available for a post-mortem look after the server has exited."""
        return self._log_path

    def start(self) -> None:
        """Launch the Joern server for this session."""
        existing = pid_on_port(self.port)
        if existing is not None:
            raise RuntimeError(
                f"Port {self.port} is already in use by PID {existing}. "
                f"Stop it first, or choose a different port."
            )

        joern = find_joern()
        # Explicit log file, NOT inherited stdout/stderr: `joern --server`
        # is a long-lived process that deliberately outlives this call
        # and keeps running after this Python process exits. Without an
        # explicit redirect here, it inherits whatever fd this process's
        # own stdout happens to be. That's harmless in an interactive
        # terminal, but if it's a pipe -- piped output, log capture, a
        # CI runner, anything that reads "until EOF" rather than live --
        # the server holds that pipe open indefinitely, so the reader
        # hangs forever waiting for a close that never comes, even long
        # after this process itself has exited normally.
        log_file = tempfile.NamedTemporaryFile(
            mode="w", prefix=f"joern-server-{self.port}-", suffix=".log", delete=False
        )
        self._log_path = log_file.name
        # start_new_session detaches this from the launching script's
        # process group so it isn't killed incidentally by shell/job
        # control signals unrelated to an explicit stop().
        launcher = subprocess.Popen(
            [joern, "--server", "--server-port", str(self.port)],
            start_new_session=True,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        # Popen already duplicated the fd for the child; this process's
        # own handle on the file isn't needed past this point.
        log_file.close()
        self._launcher = launcher

        # `except BaseException` (elsewhere in this method, and in every
        # caller that wraps start()) only catches KeyboardInterrupt --
        # what Ctrl-C raises. It does NOT catch SIGTERM: Python installs
        # no default handler that turns SIGTERM into a catchable
        # exception, so a process killed by SIGTERM (a closed terminal, a
        # test runner's own timeout, an IDE's stop button -- all commonly
        # send SIGTERM, not SIGINT) is torn down by the OS immediately,
        # no Python code runs, and no except block anywhere gets a
        # chance to fire. Installed as early as possible -- right after
        # the server process exists, not after start() finishes -- so
        # the whole window it could be alive is covered, including while
        # _wait_until_ready is still polling.
        self._install_signal_handlers()

        try:
            self._wait_until_ready(launcher)
            pid = pid_on_port(self.port)
            if pid is None:
                raise RuntimeError("Server did not bind the expected port after starting.")
            self._pid = pid
            self._client = self._make_client()
        except BaseException:
            # BaseException, not Exception: this has to catch
            # KeyboardInterrupt too (it does NOT subclass Exception, by
            # design, specifically so a bare `except Exception` won't
            # swallow it) -- a Ctrl-C during _wait_until_ready's polling
            # loop is a completely plausible way to land here, and it
            # needs the same cleanup as any other failure: the caller
            # never gets a JoernSession back to call stop() on, so a
            # half-started server (or the wrapper script that launches
            # the real JVM process, which is a DIFFERENT pid than
            # `launcher` -- see stop()'s own pid_on_port fallback for
            # why) would otherwise be leaked here with nothing left to
            # clean it up. `raise` below re-raises whatever it was
            # unchanged -- this only adds cleanup, never suppresses it.
            launcher.kill()
            pid = pid_on_port(self.port)
            if pid is not None:
                self._pid = pid
                self.stop()  # also restores signal handlers
            else:
                self._restore_signal_handlers()
            raise

    def _install_signal_handlers(self) -> None:
        # signal.signal is process-global and main-thread-only -- fine
        # for how this is actually used (one active session driving
        # things from the main thread), not designed to coexist with
        # multiple concurrent JoernSessions each wanting their own
        # independent SIGTERM/SIGINT handling.
        self._prev_sigterm_handler = signal.signal(signal.SIGTERM, self._handle_signal)
        self._prev_sigint_handler = signal.signal(signal.SIGINT, self._handle_signal)

    def _restore_signal_handlers(self) -> None:
        if self._prev_sigterm_handler is not None:
            signal.signal(signal.SIGTERM, self._prev_sigterm_handler)
            self._prev_sigterm_handler = None
        if self._prev_sigint_handler is not None:
            signal.signal(signal.SIGINT, self._prev_sigint_handler)
            self._prev_sigint_handler = None

    def _handle_signal(self, signum: int, frame) -> None:
        """
        Runs cleanup directly rather than relying on the signal turning
        into a Python exception some try/except up the stack happens to
        catch -- a signal handler intercepts the process no matter what
        Python code is currently executing, which try/except can't
        guarantee (SIGTERM in particular never becomes an exception at
        all without this). Restores the original handler first, then
        re-sends the same signal so the process still actually
        terminates the way the caller (Ctrl-C, a test runner's timeout,
        an IDE stop button) expected -- this only inserts cleanup ahead
        of that, it doesn't swallow the request to stop.
        """
        self.stop()
        signal.raise_signal(signum)

    def attach(self) -> None:
        """
        Attach to an already-running server on this port instead of
        launching a new one. Use when reusing a session across separate
        script invocations.
        """
        pid = pid_on_port(self.port)
        if pid is None or not is_process_alive(pid):
            raise RuntimeError(f"No live server found on port {self.port}.")
        self._pid = pid
        self._client = self._make_client()
        self._client.execute("1")  # confirm it actually responds

    def _make_client(self) -> CPGQLSClient:
        # Python 3.10+ has no implicit event loop; cpgqls_client requires
        # one to be passed explicitly or its constructor raises RuntimeError.
        loop = asyncio.new_event_loop()
        return CPGQLSClient(f"localhost:{self.port}", event_loop=loop)

    def _wait_until_ready(self, launcher: subprocess.Popen) -> None:
        """
        Poll until the server responds to a query, or time out.
        """
        deadline = time.monotonic() + self.startup_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if launcher.poll() is not None and launcher.returncode != 0:
                raise RuntimeError(
                    f"joern launcher exited early (code {launcher.returncode}). "
                    f"See server log at {self._log_path}"
                )
            try:
                self._make_client().execute("1")
                return
            except Exception as e:
                last_error = e
                time.sleep(0.5)
        raise TimeoutError(
            f"Joern server did not become ready in time: {last_error}. "
            f"See server log at {self._log_path}"
        )

    def load_cpg(self, cpg_path: str) -> None:
        """Load a CPG file into this session's running server."""
        self.query(f'importCpg("{cpg_path}")')

    def refresh_cpg(self, cpg_path: str) -> None:
        """
        Load a newly-parsed CPG into the same running server, replacing
        the previously loaded graph. The JVM process itself is not
        restarted — only the in-memory graph is swapped.
        """
        self.load_cpg(cpg_path)

    def query(self, cpgql: str) -> str:
        """Run a CPGQL query against the currently loaded graph."""
        self._require_started()
        result = self._client.execute(cpgql)
        self._raise_if_error(result)
        return result["stdout"]

    def query_json(self, cpgql: str) -> JsonValue:
        """
        Run a single CPGQL expression and return its result as a parsed
        Python object, instead of the raw REPL echo `query()` returns. See
        the module-level note on `_JSON_PAYLOAD_RE` for why this is needed.
        """
        raw = self.query(f"({cpgql}).toJsonPretty")
        return self._parse_repl_json(raw)

    def query_script_json(self, script: str) -> JsonValue:
        """
        Like query_json, but for a full multi-statement script (e.g. loaded
        from a .sc file) rather than a single expression. The script itself
        is responsible for arranging its FINAL top-level statement to be a
        JSON string value -- e.g. `val result: String = ujson.write(x, indent = 2)`
        -- since that's the only thing this can recover: println() output
        does NOT reach here (it goes to the server process's own console,
        not the JSON-RPC response), only the REPL's echo of the final
        statement's value does.
        """
        raw = self.query(script)
        return self._parse_repl_json(raw)

    @staticmethod
    def _parse_repl_json(raw: str) -> JsonValue:
        clean = _ANSI_RE.sub("", raw)
        match = _JSON_PAYLOAD_RE.search(clean)
        if not match:
            raise RuntimeError(f"Could not find JSON payload in REPL output: {clean!r}")
        return json.loads(match.group(1))

    def stop(self) -> None:
        """Terminate the real server process and release its memory."""
        # Restored FIRST, before the slow kill-and-wait below: stop() can
        # itself be running from inside _handle_signal, and the wait loop
        # blocks for up to ~10s with _handle_signal still installed. A
        # second SIGTERM/SIGINT landing during that window (an impatient
        # repeat Ctrl-C, a supervisor that retries) would otherwise
        # re-enter _handle_signal -> stop() without the first call ever
        # returning, stacking a new frame each time instead of replacing
        # it, until it blows the recursion limit. Restoring up front means
        # a repeat signal falls through to the real original handler
        # instead of recursing back into this code. Harmless to call even
        # if _install_signal_handlers() was never called (both
        # _prev_*_handler stay None, so this is a no-op).
        self._restore_signal_handlers()

        pid = self._pid or pid_on_port(self.port)
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(20):  # up to ~10s grace period
                    if not is_process_alive(pid):
                        break
                    time.sleep(0.5)
                else:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # already gone
        elif self._launcher is not None and self._launcher.poll() is None:
            # Nothing ever bound the port (e.g. stopped mid-startup,
            # before the JVM got that far) -- the launcher itself is
            # still the only handle left to clean up.
            self._launcher.kill()
        self._pid = None
        self._client = None
        self._launcher = None

    def _require_started(self) -> None:
        if self._client is None:
            raise RuntimeError("Session not started. Call start() or attach() first.")

    @staticmethod
    def _raise_if_error(result: dict) -> None:
        if result.get("stderr"):
            raise RuntimeError(f"Joern query error: {result['stderr']}")
