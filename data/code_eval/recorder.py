from __future__ import annotations

import json
import platform
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping

from .models import LLMCallRecord, RunRecord, StageRecord


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
                success=error_type is None,
                input_stats=dict(input_stats or {}),
                output_stats=dict(output_stats or {}),
                error_type=error_type,
            ))

    def record_llm_call(self, event: Mapping[str, object]) -> None:
        fields = {field.name for field in LLMCallRecord.__dataclass_fields__.values()}
        self.run.llm_calls.append(LLMCallRecord(**{
            key: value for key, value in event.items() if key in fields
        }))

    def write_json(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(json.dumps(self.run.to_dict(), indent=2), encoding="utf-8")
        temporary.replace(output)
        return output
