from pathlib import Path

import pytest

from gp_agent.jobs import MAX_JOBS, JobQueue, JobStatus
from gp_agent.runner import RunnerError


class InlineExecutor:
    """Runs submitted work immediately so tests stay synchronous."""

    def submit(self, fn, *args):
        fn(*args)


class DeferredExecutor:
    def __init__(self):
        self.pending = []

    def submit(self, fn, *args):
        self.pending.append((fn, args))

    def run_all(self):
        for fn, args in self.pending:
            fn(*args)


def test_successful_job_reports_file():
    queue = JobQueue(lambda req: Path("/out") / f"{req}.mp4", executor=InlineExecutor())
    done = queue.get(queue.submit("a").id)
    assert done.status is JobStatus.DONE
    assert done.file == Path("/out/a.mp4")
    assert done.error is None


def test_job_is_queued_until_worker_runs():
    ex = DeferredExecutor()
    queue = JobQueue(lambda req: Path("x.mp4"), executor=ex)
    job = queue.submit("a")
    assert job.status is JobStatus.QUEUED
    assert queue.get(job.id).status is JobStatus.QUEUED
    ex.run_all()
    assert queue.get(job.id).status is JobStatus.DONE


def test_running_state_visible_during_work():
    seen = []
    ex = DeferredExecutor()

    def work(req):
        seen.append(queue.get(job_id).status)
        return Path("x.mp4")

    queue = JobQueue(work, executor=ex)
    job_id = queue.submit("a").id
    ex.run_all()
    assert seen == [JobStatus.RUNNING]


@pytest.mark.parametrize("exc", [RunnerError("yt-dlp falhou"), ValueError("url inválido")])
def test_expected_errors_are_reported(exc):
    def work(req):
        raise exc

    queue = JobQueue(work, executor=InlineExecutor())
    job = queue.get(queue.submit("a").id)
    assert job.status is JobStatus.ERROR
    assert job.error == str(exc)


def test_unexpected_errors_are_generic():
    def work(req):
        raise RuntimeError("secret internals")

    queue = JobQueue(work, executor=InlineExecutor())
    job = queue.get(queue.submit("a").id)
    assert job.status is JobStatus.ERROR
    assert "secret" not in job.error


def test_unknown_job():
    assert JobQueue(lambda r: Path("x"), executor=InlineExecutor()).get("nope") is None


def test_ids_are_unique_and_unguessable():
    queue = JobQueue(lambda r: Path("x"), executor=InlineExecutor())
    ids = {queue.submit(i).id for i in range(20)}
    assert len(ids) == 20
    assert all(len(i) >= 16 for i in ids)


def test_old_jobs_are_forgotten():
    queue = JobQueue(lambda r: Path("x"), executor=InlineExecutor())
    first = queue.submit(0).id
    for i in range(MAX_JOBS):
        queue.submit(i)
    assert queue.get(first) is None
