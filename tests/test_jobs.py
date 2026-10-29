from pathlib import Path

import pytest

from gp_agent.jobs import MAX_JOBS, JobQueue, JobStatus
from gp_agent.runner import Cancelled
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
    queue = JobQueue(lambda req, ctx: Path("/out") / f"{req}.mp4", executor=InlineExecutor())
    done = queue.get(queue.submit("a").id)
    assert done.status is JobStatus.DONE
    assert done.file == Path("/out/a.mp4")
    assert done.error is None


def test_job_is_queued_until_worker_runs():
    ex = DeferredExecutor()
    queue = JobQueue(lambda req, ctx: Path("x.mp4"), executor=ex)
    job = queue.submit("a")
    assert job.status is JobStatus.QUEUED
    assert queue.get(job.id).status is JobStatus.QUEUED
    ex.run_all()
    assert queue.get(job.id).status is JobStatus.DONE


def test_running_state_visible_during_work():
    seen = []
    ex = DeferredExecutor()

    def work(req, ctx):
        seen.append(queue.get(job_id).status)
        return Path("x.mp4")

    queue = JobQueue(work, executor=ex)
    job_id = queue.submit("a").id
    ex.run_all()
    assert seen == [JobStatus.RUNNING]


@pytest.mark.parametrize("exc", [RunnerError("yt-dlp falhou"), ValueError("url inválido")])
def test_expected_errors_are_reported(exc):
    def work(req, ctx):
        raise exc

    queue = JobQueue(work, executor=InlineExecutor())
    job = queue.get(queue.submit("a").id)
    assert job.status is JobStatus.ERROR
    assert job.error == str(exc)


def test_unexpected_errors_are_generic():
    def work(req, ctx):
        raise RuntimeError("secret internals")

    queue = JobQueue(work, executor=InlineExecutor())
    job = queue.get(queue.submit("a").id)
    assert job.status is JobStatus.ERROR
    assert "secret" not in job.error


def test_unknown_job():
    assert JobQueue(lambda r, ctx: Path("x"), executor=InlineExecutor()).get("nope") is None


def test_ids_are_unique_and_unguessable():
    queue = JobQueue(lambda r, ctx: Path("x"), executor=InlineExecutor())
    ids = {queue.submit(i).id for i in range(20)}
    assert len(ids) == 20
    assert all(len(i) >= 16 for i in ids)


def test_old_jobs_are_forgotten():
    queue = JobQueue(lambda r, ctx: Path("x"), executor=InlineExecutor())
    first = queue.submit(0).id
    for i in range(MAX_JOBS):
        queue.submit(i)
    assert queue.get(first) is None


def test_progress_is_reported_and_clamped():
    seen = []

    def work(req, ctx):
        ctx.report(0.5)
        seen.append(queue.get(job_id).progress)
        ctx.report(7)
        seen.append(queue.get(job_id).progress)
        return Path("x.mp4")

    ex = DeferredExecutor()
    queue = JobQueue(work, executor=ex)
    job_id = queue.submit("a").id
    assert queue.get(job_id).progress is None
    ex.run_all()
    assert seen == [0.5, 1.0]
    assert queue.get(job_id).progress == 1.0


def test_cancel_before_start_skips_the_work():
    ran = []
    ex = DeferredExecutor()
    queue = JobQueue(lambda req, ctx: ran.append(req) or Path("x"), executor=ex)
    job_id = queue.submit("a").id
    assert queue.cancel(job_id) is True
    ex.run_all()
    assert ran == []
    assert queue.get(job_id).status is JobStatus.CANCELLED


def test_cancel_while_running_is_seen_by_the_work():
    def work(req, ctx):
        assert ctx.cancelled is False
        queue.cancel(job_id)
        assert ctx.cancelled is True
        raise Cancelled()

    ex = DeferredExecutor()
    queue = JobQueue(work, executor=ex)
    job_id = queue.submit("a").id
    ex.run_all()
    job = queue.get(job_id)
    assert job.status is JobStatus.CANCELLED
    assert job.error is None


def test_finished_or_unknown_jobs_cannot_be_cancelled():
    queue = JobQueue(lambda r, ctx: Path("x"), executor=InlineExecutor())
    done = queue.submit("a").id
    assert queue.cancel(done) is False
    assert queue.cancel("nope") is False
    assert queue.get(done).status is JobStatus.DONE
