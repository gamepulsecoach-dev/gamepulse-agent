"""Background clip jobs: one worker, so big downloads never run in parallel."""

import logging
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from gp_agent.runner import RunnerError

MAX_JOBS = 200
GENERIC_ERROR = "Erro inesperado no agente"

log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True)
class Job:
    id: str
    status: JobStatus
    file: Path | None = None
    error: str | None = None


class Executor(Protocol):
    def submit(self, fn: Callable[..., Any], *args: Any) -> Any: ...


class JobQueue:
    def __init__(self, work: Callable[[Any], Path], executor: Executor | None = None):
        self._work = work
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="gp-job")
        self._lock = threading.Lock()
        self._jobs: OrderedDict[str, Job] = OrderedDict()

    def submit(self, request: Any) -> Job:
        job = Job(id=secrets.token_urlsafe(12), status=JobStatus.QUEUED)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS:
                self._jobs.popitem(last=False)
        self._executor.submit(self._run, job.id, request)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is not None:
                self._jobs[job_id] = replace(current, **changes)

    def _run(self, job_id: str, request: Any) -> None:
        self._update(job_id, status=JobStatus.RUNNING)
        try:
            path = self._work(request)
        except (RunnerError, ValueError) as exc:
            self._update(job_id, status=JobStatus.ERROR, error=str(exc))
        except Exception:
            log.exception("job %s failed", job_id)
            self._update(job_id, status=JobStatus.ERROR, error=GENERIC_ERROR)
        else:
            self._update(job_id, status=JobStatus.DONE, file=path)
