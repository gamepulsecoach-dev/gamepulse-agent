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

from gp_agent.runner import Cancelled, RunnerError

MAX_JOBS = 200
GENERIC_ERROR = "Erro inesperado no agente"

log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


_FINISHED = frozenset({JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED})


@dataclass(frozen=True)
class Job:
    id: str
    status: JobStatus
    file: Path | None = None
    error: str | None = None
    progress: float | None = None  # 0..1, only for work that can measure it


class JobContext:
    """What a running job sees of its queue: report progress, notice a cancel."""

    def __init__(self, cancel: threading.Event, report: Callable[[float], None]):
        self._cancel = cancel
        self._report = report

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def report(self, fraction: float) -> None:
        self._report(max(0.0, min(1.0, fraction)))


class Executor(Protocol):
    def submit(self, fn: Callable[..., Any], *args: Any) -> Any: ...


class JobQueue:
    def __init__(self, work: Callable[[Any, JobContext], Path], executor: Executor | None = None):
        self._work = work
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="gp-job")
        self._lock = threading.Lock()
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._cancels: dict[str, threading.Event] = {}

    def submit(self, request: Any) -> Job:
        job = Job(id=secrets.token_urlsafe(12), status=JobStatus.QUEUED)
        with self._lock:
            self._jobs[job.id] = job
            self._cancels[job.id] = threading.Event()
            while len(self._jobs) > MAX_JOBS:
                old, _ = self._jobs.popitem(last=False)
                self._cancels.pop(old, None)
        self._executor.submit(self._run, job.id, request)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Asks a queued or running job to stop; False when there is nothing to stop."""
        with self._lock:
            job = self._jobs.get(job_id)
            event = self._cancels.get(job_id)
            if job is None or event is None or job.status in _FINISHED:
                return False
            event.set()
            return True

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is not None:
                self._jobs[job_id] = replace(current, **changes)

    def _finish(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            self._cancels.pop(job_id, None)
        self._update(job_id, **changes)

    def _run(self, job_id: str, request: Any) -> None:
        with self._lock:
            event = self._cancels.get(job_id)
        if event is None or event.is_set():
            self._finish(job_id, status=JobStatus.CANCELLED)
            return
        self._update(job_id, status=JobStatus.RUNNING)
        ctx = JobContext(event, lambda p: self._update(job_id, progress=p))
        try:
            path = self._work(request, ctx)
        except Cancelled:
            self._finish(job_id, status=JobStatus.CANCELLED)
        except (RunnerError, ValueError) as exc:
            self._finish(job_id, status=JobStatus.ERROR, error=str(exc))
        except Exception:
            log.exception("job %s failed", job_id)
            self._finish(job_id, status=JobStatus.ERROR, error=GENERIC_ERROR)
        else:
            self._finish(job_id, status=JobStatus.DONE, file=path)
