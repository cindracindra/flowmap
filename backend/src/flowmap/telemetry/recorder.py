from __future__ import annotations

import json
import platform
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping

from .models import LLMBatchRecord, LLMCallRecord, RunRecord, StageRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvaluationRecorder:
    """Collect one evaluation run and write one self-contained JSON artifact."""

    def __init__(self, *, run_id: str | None = None, manifest: Mapping | None = None):
        base_manifest = {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        }
        base_manifest.update(dict(manifest or {}))
        self.run = RunRecord(
            run_id=run_id or uuid.uuid4().hex,
            started_at=_now(),
            manifest=base_manifest,
        )
        self._started = time.perf_counter()
        self._rss_lock = threading.Lock()
        self._rss_peak = 0
        self._stage_rss_peak = 0
        self._sampling = True
        self._sampler = threading.Thread(target=self._sample_memory, daemon=True)
        self._sampler.start()

    @staticmethod
    def _process_tree_rss_bytes() -> int | None:
        """Return RSS for this process and descendants without extra packages."""
        try:
            result = subprocess.run(
                ["ps", "-axo", "pid=,ppid=,rss="],
                check=True,
                capture_output=True,
                text=True,
            )
            rows = [tuple(map(int, line.split())) for line in result.stdout.splitlines()]
            children: dict[int, list[int]] = {}
            rss: dict[int, int] = {}
            for pid, ppid, rss_kib in rows:
                children.setdefault(ppid, []).append(pid)
                rss[pid] = rss_kib * 1024
            pending = [os.getpid()]
            descendants: set[int] = set()
            while pending:
                pid = pending.pop()
                if pid in descendants:
                    continue
                descendants.add(pid)
                pending.extend(children.get(pid, ()))
            return sum(rss.get(pid, 0) for pid in descendants)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    def _sample_memory(self) -> None:
        while self._sampling:
            value = self._process_tree_rss_bytes()
            if value is not None:
                with self._rss_lock:
                    self._rss_peak = max(self._rss_peak, value)
                    self._stage_rss_peak = max(self._stage_rss_peak, value)
            time.sleep(0.25)

    @contextmanager
    def stage(
        self,
        name: str,
        *,
        input_stats: Mapping[str, int | float | str | None] | None = None,
        output_stats: dict[str, int | float | str | None] | None = None,
    ) -> Iterator[None]:
        started_at = _now()
        start = time.perf_counter()
        cpu_start = time.process_time()
        with self._rss_lock:
            self._stage_rss_peak = self._process_tree_rss_bytes() or 0
        error_type = None
        try:
            yield
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            self.run.stages.append(StageRecord(
                name=name,
                started_at=started_at,
                duration_seconds=time.perf_counter() - start,
                process_cpu_seconds=time.process_time() - cpu_start,
                peak_process_tree_rss_bytes=self._stage_peak(),
                success=error_type is None,
                input_stats=dict(input_stats or {}),
                output_stats=dict(output_stats or {}),
                error_type=error_type,
            ))

    def _stage_peak(self) -> int | None:
        with self._rss_lock:
            return self._stage_rss_peak or None

    def finish(self, *, success: bool = True) -> None:
        if self.run.finished_at is not None:
            return
        self._sampling = False
        self._sampler.join(timeout=1.0)
        self.run.finished_at = _now()
        self.run.duration_seconds = time.perf_counter() - self._started
        self.run.success = success
        with self._rss_lock:
            self.run.peak_process_tree_rss_bytes = self._rss_peak or None
        self.run.totals = {
            "stages": len(self.run.stages),
            "successful_stages": sum(stage.success for stage in self.run.stages),
            "llm_requests": len(self.run.llm_calls),
            "successful_llm_requests": sum(call.success for call in self.run.llm_calls),
            "llm_input_tokens": sum(call.input_tokens or 0 for call in self.run.llm_calls),
            "llm_output_tokens": sum(call.output_tokens or 0 for call in self.run.llm_calls),
            "llm_total_tokens": sum(call.total_tokens or 0 for call in self.run.llm_calls),
            "llm_retry_items": sum(batch.retry_items for batch in self.run.llm_batches),
            "llm_unresolved_items": sum(batch.unresolved_items for batch in self.run.llm_batches),
            "llm_oversized_items": sum(batch.oversized_items for batch in self.run.llm_batches),
        }

    def record_llm_call(self, event: Mapping[str, object]) -> None:
        fields = {field.name for field in LLMCallRecord.__dataclass_fields__.values()}
        self.run.llm_calls.append(LLMCallRecord(**{
            key: value for key, value in event.items() if key in fields
        }))

    def record_llm_batch(self, event: Mapping[str, object]) -> None:
        fields = {field.name for field in LLMBatchRecord.__dataclass_fields__.values()}
        self.run.llm_batches.append(LLMBatchRecord(**{
            key: value for key, value in event.items() if key in fields
        }))

    def write_json(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(json.dumps(self.run.to_dict(), indent=2), encoding="utf-8")
        temporary.replace(output)
        return output
