"""Working files (per-segment WAVs, old samples, orphan dirs) must not pile up in DATA_DIR."""
import asyncio

import pytest

from mytts import config
from mytts.contracts import JobStatus, SampleRequest
from mytts.pipeline import cleanup, scheduler as scheduler_mod
from tests.fixtures.fake_worker import FakeWorker


async def _wait(pred, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        if pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timeout")


def _wavs(path):
    return sorted(p.name for p in path.rglob("*.wav")) if path.exists() else []


@pytest.fixture(autouse=True)
def no_grace(monkeypatch):
    monkeypatch.setattr(scheduler_mod, "CHAPTER_WAV_GRACE_S", 0)
    monkeypatch.setattr(cleanup, "SAMPLE_WAV_GRACE_S", 0)


async def test_finished_job_leaves_no_segment_wavs(scheduler_factory, sample_book_txt):
    sch = scheduler_factory(FakeWorker())
    await sch.start()
    try:
        info = await sch.create_job(sample_book_txt, "sample_book.txt")
        await sch.start_job(info.id)
        await _wait(lambda: sch.get_job(info.id).status == JobStatus.done)
        workdir = config.DATA_DIR / "jobs" / info.id
        await _wait(lambda: not _wavs(workdir / "raw") and not _wavs(workdir / "seg"))
        chapters = [sch.store.get_chapter_file(info.id, c.index) for c in sch.get_job(info.id).chapters]
        assert any(chapters), "chapter audio files must remain"
        assert (workdir / "book.json").exists()  # needed for "Convert again"
    finally:
        await sch.stop()


async def test_sample_history_is_bounded(scheduler_factory, sample_book_txt):
    sch = scheduler_factory(FakeWorker())
    await sch.start()
    try:
        info = await sch.create_job(sample_book_txt, "sample_book.txt")
        for _ in range(cleanup.KEEP_SAMPLES_PER_JOB + 3):
            smp = await sch.create_sample(info.id, SampleRequest(seconds=10))
            await _wait(lambda sid=smp.id: sch.get_sample(info.id, sid).status == "done")
        assert len(sch.list_samples(info.id)) == cleanup.KEEP_SAMPLES_PER_JOB
        dirs = [d for d in (config.DATA_DIR / "jobs" / info.id / "samples").iterdir()]
        assert len(dirs) == cleanup.KEEP_SAMPLES_PER_JOB
    finally:
        await sch.stop()


async def test_startup_cleanup_removes_leftovers(scheduler_factory, sample_book_txt):
    sch = scheduler_factory(FakeWorker())
    await sch.start()
    info = await sch.create_job(sample_book_txt, "sample_book.txt")
    await sch.start_job(info.id)
    await _wait(lambda: sch.get_job(info.id).status == JobStatus.done)
    await sch.stop()
    workdir = config.DATA_DIR / "jobs" / info.id
    (workdir / "seg").mkdir(exist_ok=True)
    (workdir / "seg" / "left_by_crash.wav").write_bytes(b"x")          # crash leftover
    orphan = config.DATA_DIR / "jobs" / "job_deadbeef0000"
    (orphan / "raw").mkdir(parents=True)
    (orphan / "raw" / "x.wav").write_bytes(b"x")                        # job no longer in the DB

    sch2 = scheduler_factory(FakeWorker())
    await sch2.start()
    try:
        assert not orphan.exists()
        assert not _wavs(workdir / "seg")
        assert (workdir / "book.json").exists()
    finally:
        await sch2.stop()
