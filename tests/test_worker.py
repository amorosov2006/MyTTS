import os
import asyncio

import pytest

from mytts.config import QA_MAX_ATTEMPTS, QA_MAX_CER
from mytts.contracts import Lang, SynthesisItem, SynthesisParams, WorkerCrashed
from mytts.pipeline.worker import InProcessWorker, ProcessWorker, process_batch
from mytts.tts.fake import FakeEngine, FakeVerifier


def _items(texts, out_dir, lang=Lang.ru):
    return [SynthesisItem(segment_id=f"s{i}", text=t, lang=lang, out_wav=str(out_dir / f"s{i}.wav"))
            for i, t in enumerate(texts)]


# --------------------------------------------------------------------------------- process_batch

def test_process_batch_basic(voice_ru, tmp_path):
    engine = FakeEngine()
    engine.load()
    items = _items(["Привет, мир.", "Как дела?"], tmp_path)
    results = process_batch(engine, None, items, voice_ru, SynthesisParams(), qa=False)
    assert len(results) == 2
    assert all(r.ok and r.attempts == 1 and r.cer is None for r in results)
    assert all(tmp_path.joinpath(f"{r.segment_id}.wav").exists() for r in results)


def test_process_batch_qa_retries_and_keeps_best(voice_ru, tmp_path):
    # FakeVerifier marks "bad" as cer=0.5 every time; QA should retry up to QA_MAX_ATTEMPTS,
    # then keep the (only, still-bad) attempt as the best -- ok=True with cer reported.
    engine = FakeEngine()
    engine.load()
    verifier = FakeVerifier(bad_texts=["bad"])
    items = _items(["good", "bad"], tmp_path)
    results = process_batch(engine, verifier, items, voice_ru, SynthesisParams(), qa=True)
    by_id = {r.segment_id: r for r in results}
    assert by_id["s0"].ok and by_id["s0"].cer == 0.0 and by_id["s0"].attempts == 1
    assert by_id["s1"].ok and by_id["s1"].cer == 0.5 and by_id["s1"].attempts == QA_MAX_ATTEMPTS
    assert by_id["s1"].cer > QA_MAX_CER
    # each retry is a smaller batch containing only the still-failing item
    assert engine.calls[-1] == ["bad"]


def test_process_batch_hit_token_cap_forces_retry(voice_ru, tmp_path):
    engine = FakeEngine(cap_texts=["capped"])
    engine.load()
    items = _items(["capped"], tmp_path)
    results = process_batch(engine, None, items, voice_ru, SynthesisParams(), qa=False)
    assert results[0].ok
    assert results[0].attempts == QA_MAX_ATTEMPTS  # kept retrying since hit_token_cap every time


def test_process_batch_hard_failure_is_isolated_and_ok_false(voice_ru, tmp_path):
    # One item always raises; the batch call for [good, bad] must not lose "good".
    engine = FakeEngine(fail_texts=["bad"])
    engine.load()
    items = _items(["good", "bad"], tmp_path)
    results = process_batch(engine, None, items, voice_ru, SynthesisParams(), qa=False)
    by_id = {r.segment_id: r for r in results}
    assert by_id["s0"].ok and by_id["s0"].out_wav is not None
    assert not by_id["s1"].ok and by_id["s1"].out_wav is None and by_id["s1"].error


def test_process_batch_empty():
    assert process_batch(FakeEngine(), None, [], None, SynthesisParams(), qa=False) == []


# --------------------------------------------------------------------------------- InProcessWorker

async def test_inprocess_worker_roundtrip(voice_ru, tmp_path):
    worker = InProcessWorker(FakeEngine(), FakeVerifier())
    await worker.start()
    assert worker.status().state == "idle"
    items = _items(["Привет, мир."], tmp_path)
    results = await worker.synthesize(items, voice_ru, SynthesisParams(), qa=True)
    assert results[0].ok and results[0].cer == 0.0
    await worker.stop()
    assert worker.status().state == "stopped"


# --------------------------------------------------------------------------------- ProcessWorker

async def test_process_worker_fake_roundtrip(voice_ru, tmp_path):
    worker = ProcessWorker(engine="fake", mem_cap_gb=4.0, call_timeout_s=30)
    await worker.start()
    try:
        assert worker.status().state == "idle"
        items = _items(["Привет, мир."], tmp_path)
        results = await worker.synthesize(items, voice_ru, SynthesisParams(), qa=False)
        assert results[0].ok
        assert tmp_path.joinpath("s0.wav").exists()
        status = worker.status()
        assert status.footprint_gb >= 0.0
    finally:
        await worker.stop()
    assert worker.status().state == "stopped"


async def test_process_worker_memory_cap_kill_then_restart(voice_ru, tmp_path):
    worker = ProcessWorker(engine="fake", mem_cap_gb=0.3, call_timeout_s=10, poll_interval_s=0.1)
    await worker.start()
    try:
        with pytest.raises(WorkerCrashed):
            await worker._debug("test_alloc", gb=1.0)
        # supervisor should have restarted the worker automatically
        for _ in range(50):
            if worker.status().state == "idle":
                break
            await asyncio.sleep(0.1)
        assert worker.status().state == "idle"
        await asyncio.sleep(0.5)  # a buggy double-restart would show up here
        assert worker.status().restarts == 1
        supervisors = [t for t in asyncio.all_tasks() if "_supervise" in repr(t.get_coro())]
        assert len(supervisors) == 1

        items = _items(["Привет, мир."], tmp_path)
        results = await worker.synthesize(items, voice_ru, SynthesisParams(), qa=False)
        assert results[0].ok
    finally:
        await worker.stop()


async def test_process_worker_timeout_kill(voice_ru, tmp_path):
    worker = ProcessWorker(engine="fake", mem_cap_gb=4.0, call_timeout_s=0.5, poll_interval_s=0.1)
    await worker.start()
    try:
        with pytest.raises(WorkerCrashed):
            await worker._debug("test_sleep", seconds=5)
        for _ in range(50):
            if worker.status().state == "idle":
                break
            await asyncio.sleep(0.1)
        assert worker.status().state == "idle"
    finally:
        await worker.stop()


async def test_process_worker_stop_leaves_no_child(voice_ru):
    worker = ProcessWorker(engine="fake", mem_cap_gb=4.0)
    await worker.start()
    pid = worker._proc.pid
    await worker.stop()
    assert worker._proc is None
    import os
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_process_worker_refuses_start_without_headroom(monkeypatch):
    import mytts.pipeline.worker as worker_mod
    monkeypatch.setattr(worker_mod, "available_bytes", lambda: 1 * 1024**3)  # 1 GB "available"
    worker = ProcessWorker(engine="fake", mem_cap_gb=14.0)
    with pytest.raises(WorkerCrashed):
        await worker.start()
    assert worker.status().state == "failed"


def test_child_exits_when_app_is_killed_mid_batch():
    """SIGKILL the app while the worker is busy: the child must not keep running unsupervised."""
    import signal
    import subprocess
    import sys
    import time as _t

    code = r'''
import asyncio
from mytts.pipeline.worker import ProcessWorker

async def main():
    w = ProcessWorker(engine="fake", call_timeout_s=60)
    await w.start()
    print(w._proc.pid, flush=True)
    await w._debug("test_sleep", seconds=60)   # busy: not polling the request loop

asyncio.run(main())
'''
    app = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    child = int(app.stdout.readline())
    _t.sleep(1.0)
    os.kill(app.pid, signal.SIGKILL)
    app.wait()
    for _ in range(40):
        if subprocess.run(["kill", "-0", str(child)], capture_output=True).returncode != 0:
            return
        _t.sleep(0.1)
    os.kill(child, signal.SIGKILL)
    raise AssertionError("worker child survived the app being killed")
