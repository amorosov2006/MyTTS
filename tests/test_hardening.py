"""Regressions from the second code review (Gemini engine, pause/resume, PDF, keys)."""
import asyncio

import httpx
import pytest

from mytts.contracts import EngineName, JobSettings, JobStatus, Lang, SynthesisItem, SynthesisParams
from mytts.ingest.pdf import _dehyphenate
from mytts.pipeline.cloud_worker import GeminiWorker, gemini_voice
from mytts.pipeline.scheduler import ConflictError
from tests.fixtures.fake_worker import FakeWorker
from tests.test_gemini import _wav_b64


async def _wait(pred, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        if pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timeout")


def _items(tmp_path, n):
    return [SynthesisItem(segment_id=f"s{i}", text=f"Предложение номер {i}, достаточно длинное.",
                          lang=Lang.ru, out_wav=str(tmp_path / f"s{i}.wav")) for i in range(n)]


def _ok(text):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "audio/wav", "data": _wav_b64(max(0.5, len(text) / 14))}}]}}]})


async def test_daily_quota_mid_batch_keeps_finished_audio(tmp_path, monkeypatch):
    """Items that finished before the quota hit are kept; only the rest are retried later."""
    from mytts import config
    monkeypatch.setattr(config, "GEMINI_CONCURRENCY", 1)
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        body = request.read().decode()
        if calls["n"] <= 3:
            import json
            return _ok(json.loads(body)["contents"][0]["parts"][0]["text"])
        return httpx.Response(429, json={"error": {"message": "quota", "details": [
            {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel", "quotaValue": "100"}]},
            {"retryDelay": "20s"}]}})  # short delay, but a per-DAY quota: must still pause

    w = GeminiWorker(key_fn=lambda: "k", transport=httpx.MockTransport(handler))
    res = await w.synthesize(_items(tmp_path, 6), gemini_voice("Kore", Lang.ru, "gemini-3.8-flash-tts"),
                             SynthesisParams(), qa=False)
    assert [r.ok for r in res[:3]] == [True] * 3
    assert all(r.retry_later for r in res[3:])
    assert calls["n"] == 4, "no new billable calls after the stop"
    assert "daily quota" in w.pause_reason and w.retry_at


async def test_network_blip_is_retried_not_fatal(tmp_path):
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("wifi dropped")
        import json
        return _ok(json.loads(request.read())["contents"][0]["parts"][0]["text"])

    w = GeminiWorker(key_fn=lambda: "k", transport=httpx.MockTransport(handler))
    res = await w.synthesize(_items(tmp_path, 1), gemini_voice("Kore", Lang.ru, "gemini-3.8-flash-tts"),
                             SynthesisParams(), qa=False)
    assert res[0].ok and w.pause_reason is None


async def test_quota_pause_requeues_only_unfinished_segments(scheduler_factory, sample_book_txt, monkeypatch):
    from mytts import config
    monkeypatch.setattr(config, "GEMINI_CONCURRENCY", 1)
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            import json
            return _ok(json.loads(request.read())["contents"][0]["parts"][0]["text"])
        return httpx.Response(429, json={"error": {"message": "q", "details": [
            {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}, {"retryDelay": "68900s"}]}})

    sch = scheduler_factory(FakeWorker())
    sch.cloud_worker = GeminiWorker(key_fn=lambda: "k", transport=httpx.MockTransport(handler))
    await sch.start()
    try:
        info = await sch.create_job(sample_book_txt, "sample_book.txt")
        sch.update_settings(info.id, JobSettings(engine=EngineName.gemini))
        await sch.start_job(info.id)
        await _wait(lambda: sch.get_job(info.id).status == JobStatus.paused)
        await _wait(lambda: sch.store.job_segment_totals(info.id).get("done", 0) == 2)
        totals = sch.store.job_segment_totals(info.id)
        assert totals.get("failed", 0) == 0 and totals.get("running", 0) == 0
        assert info.id in sch._auto_resume
        sch.cancel_job(info.id)
        assert info.id not in sch._auto_resume, "cancel must stop the auto-resume timer"
    finally:
        await sch.stop()


async def test_untick_then_retick_chapter_is_converted(scheduler_factory, sample_book_txt):
    sch = scheduler_factory(FakeWorker(delay=0.2))
    await sch.start()
    try:
        info = await sch.create_job(sample_book_txt, "sample_book.txt")
        last = info.chapters[-1].index
        await sch.start_job(info.id)
        sch.pause_job(info.id)
        sch.update_chapters(info.id, [{"index": last, "include": False}])
        await sch.resume_job(info.id)
        sch.pause_job(info.id)
        sch.update_chapters(info.id, [{"index": last, "include": True}])
        await sch.resume_job(info.id)
        await _wait(lambda: sch.get_job(info.id).status == JobStatus.done, timeout=20)
        ch = next(c for c in sch.get_job(info.id).chapters if c.index == last)
        assert ch.status == "done" and ch.segments_done == ch.segments_total > 0
    finally:
        await sch.stop()


async def test_engine_cannot_change_after_segments_exist(scheduler_factory, sample_book_txt):
    sch = scheduler_factory(FakeWorker(delay=0.3))
    await sch.start()
    try:
        info = await sch.create_job(sample_book_txt, "sample_book.txt")
        await sch.start_job(info.id)
        sch.pause_job(info.id)
        with pytest.raises(ConflictError):
            sch.update_settings(info.id, JobSettings(engine=EngineName.gemini))
    finally:
        await sch.stop()


async def test_unusable_output_folder_keeps_job_startable(scheduler_factory, sample_book_txt, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    sch = scheduler_factory(FakeWorker())
    await sch.start()
    try:
        info = await sch.create_job(sample_book_txt, "sample_book.txt")
        sch.update_settings(info.id, JobSettings(output_dir=str(blocker)))
        with pytest.raises(ConflictError):
            await sch.start_job(info.id)
        assert sch.get_job(info.id).status == JobStatus.parsed
        assert not sch.store.chapter_indexes_with_segments(info.id)
    finally:
        await sch.stop()


def test_pdf_number_range_across_lines_is_not_glued():
    assert _dehyphenate(["Война 1941-", "1945 гг. закончилась."]) == ["Война 1941-1945 гг. закончилась."]


def test_unrelated_google_api_key_env_is_ignored(monkeypatch):
    from mytts import keystore
    monkeypatch.setenv("GOOGLE_API_KEY", "some-other-project-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert keystore.gemini_key_status()["source"] != "GOOGLE_API_KEY"
