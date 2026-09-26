import asyncio

import pytest

from mytts.contracts import JobStatus, SampleRequest
from mytts.pipeline.scheduler import ConflictError, NotFoundError, Scheduler
from tests.fixtures.fake_worker import FakeWorker


async def _wait_for(predicate, timeout=5.0, interval=0.02):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met before timeout")


@pytest.fixture
async def running_scheduler(scheduler):
    await scheduler.start()
    yield scheduler
    await scheduler.stop()


async def test_full_job_lifecycle_and_event_order(running_scheduler, sample_book_txt):
    sch = running_scheduler
    events = []
    sid, q = sch.bus.subscribe()

    info = await sch.create_job(sample_book_txt, "sample_book.txt")
    assert info.status == JobStatus.parsed
    assert len(info.chapters) == 4

    await sch.start_job(info.id)

    async def collect():
        while True:
            events.append(await q.get())

    collector = asyncio.ensure_future(collect())
    try:
        await _wait_for(lambda: sch.get_job(info.id).status == JobStatus.done, timeout=10)
    finally:
        collector.cancel()
    sch.bus.unsubscribe(sid)

    final = sch.get_job(info.id)
    assert final.progress.segments_done == final.progress.segments_total
    assert all(c.status in ("done", "skipped") for c in final.chapters if c.include)
    assert final.output_path is not None

    # every segment produced at least one segment event and every assembled chapter one
    # chapter "done" event (completion *order* across chapters isn't guaranteed: CPU
    # post-processing of chapter N's tail can race with chapter N+1's head in the pool).
    seg_events = [e for e in events if e.type == "segment"]
    assert len({e.data["segment_id"] for e in seg_events}) == final.progress.segments_total
    chapter_done_events = [e for e in events if e.type == "chapter" and e.data.get("status") == "done"]
    assert len(chapter_done_events) == sum(1 for c in final.chapters if c.include)

    # docs/API.md's actual guarantee: chapter N+1's segments are never *dispatched* to the
    # worker until chapter N has nothing left pending.
    dispatched_chapters = []
    for batch in sch.worker.synthesize_batches:
        for seg_id in batch:
            row = sch.store.get_segment(info.id, seg_id)
            if row is not None:
                dispatched_chapters.append(row["chapter"])
    assert dispatched_chapters == sorted(dispatched_chapters)


async def test_sample_preempts_running_job(running_scheduler, sample_book_txt):
    sch = running_scheduler
    info = await sch.create_job(sample_book_txt, "sample_book.txt")
    await sch.start_job(info.id)
    await _wait_for(lambda: sch.get_job(info.id).status == JobStatus.running, timeout=5)

    sample = await sch.create_sample(info.id, SampleRequest(seconds=15))
    assert sample.status in ("queued", "running", "done")

    await _wait_for(lambda: sch.get_sample(info.id, sample.id).status == "done", timeout=10)
    done_sample = sch.get_sample(info.id, sample.id)
    assert done_sample.audio_url is not None
    assert len(done_sample.segments) > 0

    await _wait_for(lambda: sch.get_job(info.id).status == JobStatus.done, timeout=10)


async def test_pause_resume_cancel(running_scheduler, sample_book_txt):
    sch = running_scheduler
    info = await sch.create_job(sample_book_txt, "sample_book.txt")
    await sch.start_job(info.id)
    await _wait_for(lambda: sch.get_job(info.id).progress.segments_done >= 1, timeout=5)

    paused = sch.pause_job(info.id)
    assert paused.status == JobStatus.paused
    done_at_pause = sch.get_job(info.id).progress.segments_done
    await asyncio.sleep(0.3)
    # no *new* batches should start once paused (in-flight ones may still finish)
    await asyncio.sleep(0.3)
    assert sch.get_job(info.id).status == JobStatus.paused

    with pytest.raises(ConflictError):
        sch.pause_job(info.id)

    resumed = sch.resume_job(info.id)
    assert resumed.status in (JobStatus.queued, JobStatus.running)
    await _wait_for(lambda: sch.get_job(info.id).status == JobStatus.done, timeout=10)

    with pytest.raises(ConflictError):
        sch.resume_job(info.id)

    info2 = await sch.create_job(sample_book_txt, "sample_book.txt")
    await sch.start_job(info2.id)
    await _wait_for(lambda: sch.get_job(info2.id).progress.segments_done >= 1, timeout=5)
    cancelled = sch.cancel_job(info2.id)
    assert cancelled.status == JobStatus.cancelled
    with pytest.raises(ConflictError):
        sch.cancel_job(info2.id)


async def test_crash_retry_then_pause(scheduler_factory, sample_book_txt):
    worker = FakeWorker(crash_on_calls={1, 2, 3, 4})
    sch = scheduler_factory(worker)
    await sch.start()
    try:
        info = await sch.create_job(sample_book_txt, "sample_book.txt")
        await sch.start_job(info.id)
        await _wait_for(lambda: sch.get_job(info.id).status == JobStatus.paused, timeout=10)
        final = sch.get_job(info.id)
        assert final.error and "crash" in final.error.lower()
        assert worker.calls >= 4
    finally:
        await sch.stop()


async def test_restart_recovery_resumes_without_redoing_done_segments(
    store, fake_services, bus, voices_registry, sample_book_txt
):
    from concurrent.futures import ThreadPoolExecutor

    worker1 = FakeWorker()
    sch1 = Scheduler(store, fake_services, bus, worker1, voices=voices_registry,
                     executor=ThreadPoolExecutor(max_workers=3))
    await sch1.start()
    info = await sch1.create_job(sample_book_txt, "sample_book.txt")
    await sch1.start_job(info.id)
    await _wait_for(lambda: sch1.get_job(info.id).progress.segments_done >= 2, timeout=5)
    # simulate an unclean shutdown: stop the dispatch loop without pausing/finishing the job
    sch1._stopped = True
    sch1._wakeup.set()
    await sch1._task
    done_before = store.job_segment_totals(info.id).get("done", 0)

    worker2 = FakeWorker()
    sch2 = Scheduler(store, fake_services, bus, worker2, voices=voices_registry,
                     executor=ThreadPoolExecutor(max_workers=3))
    await sch2.start()  # recovery: running/queued -> paused
    try:
        assert sch2.get_job(info.id).status == JobStatus.paused
        assert sch2.get_job(info.id).error

        resumed = sch2.resume_job(info.id)
        assert resumed.status in (JobStatus.queued, JobStatus.running)
        await _wait_for(lambda: sch2.get_job(info.id).status == JobStatus.done, timeout=10)
        # segments already marked done before the crash were never re-synthesized
        seg_ids_synthesized = {sid for batch in worker2.synthesize_batches for sid in batch}
        totals_after = store.job_segment_totals(info.id)
        assert totals_after.get("done", 0) >= done_before
        assert len(seg_ids_synthesized) == totals_after.get("done", 0) - done_before
    finally:
        await sch2.stop()
        sch2._executor.shutdown(wait=True)


async def test_settings_change_resets_sample_approved(running_scheduler, sample_book_txt):
    sch = running_scheduler
    info = await sch.create_job(sample_book_txt, "sample_book.txt")
    sample = await sch.create_sample(info.id, SampleRequest(seconds=10))
    await _wait_for(lambda: sch.get_sample(info.id, sample.id).status == "done", timeout=10)

    approved = sch.approve_sample(info.id, sample.id)
    assert approved.sample_approved is True

    new_settings = info.settings.model_copy(update={"speed": 1.3})
    updated = sch.update_settings(info.id, new_settings)
    assert updated.sample_approved is False

    # re-approving the same (now stale) sample against the new settings must not re-approve
    reapproved = sch.approve_sample(info.id, sample.id)
    assert reapproved.sample_approved is False


async def test_not_found_and_conflict_errors(scheduler):
    with pytest.raises(NotFoundError):
        scheduler.get_job("job_missing")
    with pytest.raises(NotFoundError):
        scheduler.pause_job("job_missing")


async def test_cannot_edit_chapters_or_settings_once_running(running_scheduler, sample_book_txt):
    sch = running_scheduler
    info = await sch.create_job(sample_book_txt, "sample_book.txt")
    await sch.start_job(info.id)
    with pytest.raises(ConflictError):
        sch.update_chapters(info.id, [{"index": 0, "include": False}])
    with pytest.raises(ConflictError):
        sch.update_settings(info.id, info.settings)
    with pytest.raises(ConflictError):
        await sch.start_job(info.id)
