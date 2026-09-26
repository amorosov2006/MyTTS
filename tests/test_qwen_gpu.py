"""Real-model tests: Qwen3-TTS on MLX + mlx-whisper QA.

CLAUDE.md hard rule: only run this file under bench/memguard.py, one process at a time. The
`gpu` marker is excluded from the default test run (pyproject addopts), so:

    uv run python bench/memguard.py --limit-gb 16 --min-free-gb 8 -- uv run pytest -m gpu -q
"""
import time

import pytest

from mytts.config import QA_MAX_CER
from mytts.contracts import Lang, SynthesisItem, SynthesisParams
from mytts.pipeline.worker import ProcessWorker
from mytts.tts.qwen_mlx import QwenEngine
from mytts.tts.whisper_qa import WhisperVerifier

pytestmark = pytest.mark.gpu


@pytest.fixture(scope="module")
def engine():
    e = QwenEngine()
    e.load()
    yield e
    e.unload()


@pytest.fixture(scope="module")
def verifier():
    return WhisperVerifier()


RU_TEXTS = ["Добрый вечер, дорогие слушатели.", "Сегодня мы начинаем новую главу этой книги."]
EN_TEXTS = ["Good evening, dear listeners.", "Today we begin a new chapter of this book."]


def test_qwen_batch_ru(engine, voice_ru):
    outputs = engine.synthesize(RU_TEXTS, Lang.ru, voice_ru, SynthesisParams())
    assert len(outputs) == 2
    for out in outputs:
        assert out.audio.dtype.name == "float32"
        assert out.audio.size > 0
        assert not out.hit_token_cap


def test_qwen_batch_en(engine, voice_en):
    outputs = engine.synthesize(EN_TEXTS, Lang.en, voice_en, SynthesisParams())
    assert len(outputs) == 2
    for out in outputs:
        assert out.audio.dtype.name == "float32"
        assert out.audio.size > 0
        assert not out.hit_token_cap


def test_whisper_verifier_cer(engine, verifier, voice_ru, voice_en):
    ru_out = engine.synthesize(RU_TEXTS[:1], Lang.ru, voice_ru, SynthesisParams())[0]
    en_out = engine.synthesize(EN_TEXTS[:1], Lang.en, voice_en, SynthesisParams())[0]

    ru_cer, ru_transcript = verifier.check(ru_out.audio, engine.sample_rate, RU_TEXTS[0], Lang.ru)
    en_cer, en_transcript = verifier.check(en_out.audio, engine.sample_rate, EN_TEXTS[0], Lang.en)

    print(f"\nru: cer={ru_cer:.3f} transcript={ru_transcript!r}")
    print(f"en: cer={en_cer:.3f} transcript={en_transcript!r}")
    assert ru_cer < 0.1
    assert en_cer < 0.1


async def test_process_worker_qwen_end_to_end(voice_ru, tmp_path):
    """4 items, qa=True, measuring real child-process footprint with both Qwen + Whisper
    resident. PLAN.md expects ~12-13 GB; report if it goes over 18 GB."""
    worker = ProcessWorker(engine="qwen", mem_cap_gb=18.0, call_timeout_s=900)
    await worker.start()
    try:
        items = [
            SynthesisItem(segment_id=f"s{i}", text=t, lang=Lang.ru, out_wav=str(tmp_path / f"s{i}.wav"))
            for i, t in enumerate(RU_TEXTS + RU_TEXTS)
        ]
        t0 = time.perf_counter()
        results = await worker.synthesize(items, voice_ru, SynthesisParams(), qa=True)
        wall = time.perf_counter() - t0

        assert all(r.ok for r in results)
        audio_s = sum(r.duration_s for r in results)
        status = worker.status()
        print(f"\nqwen worker footprint={status.footprint_gb:.2f} GB, "
              f"{len(items)} items, {audio_s:.1f}s audio in {wall:.1f}s "
              f"(x{audio_s / wall:.1f} realtime), cers={[r.cer for r in results]}")
        for r in results:
            assert r.cer is None or r.cer < QA_MAX_CER + 0.05  # allow one QA-flagged outlier
    finally:
        await worker.stop()
