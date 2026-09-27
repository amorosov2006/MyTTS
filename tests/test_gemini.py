"""Gemini TTS engine against a simulated Google API (httpx.MockTransport) — no network."""
import asyncio
import base64
import io
import json
import os
import stat

import httpx
import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from mytts import config, keystore
from mytts.api.app import create_app
from mytts.contracts import EngineName, JobSettings, JobStatus, Lang, SynthesisItem, SynthesisParams
from mytts.pipeline.cloud_worker import GeminiWorker, gemini_voice
from mytts.tts.gemini import GeminiAuthError, GeminiClient, GeminiError
from tests.fixtures.fake_worker import FakeWorker


def _wav_b64(seconds: float, sr: int = 24000) -> str:
    buf = io.BytesIO()
    t = np.arange(int(seconds * sr)) / sr
    sf.write(buf, (0.1 * np.sin(2 * np.pi * 200 * t)).astype(np.float32), sr, format="WAV", subtype="PCM_16")
    return base64.b64encode(buf.getvalue()).decode()


class FakeGoogle:
    """generateContent + models list. Options simulate the failure modes we handle."""

    def __init__(self, *, legacy_only=False, rate_limit_first=0, bad_key=False, pcm=False, short=False):
        self.legacy_only, self.rate_limit_first = legacy_only, rate_limit_first
        self.bad_key, self.pcm, self.short = bad_key, pcm, short
        self.bodies: list[dict] = []
        self.in_flight = self.max_in_flight = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.bad_key or request.headers.get("x-goog-api-key") != "good-key":
            return httpx.Response(400, json={"error": {"message": "API key not valid. Please pass a valid API key."}})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"name": "models/gemini-3.8-flash-tts"}]})
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.rate_limit_first > 0:
            self.rate_limit_first -= 1
            return httpx.Response(429, json={"error": {"message": "quota", "details": [{"retryDelay": "0s"}]}})
        voice_cfg = body["generationConfig"]["speechConfig"]["voiceConfig"]
        if self.legacy_only and "voice" in voice_cfg:
            return httpx.Response(400, json={"error": {"message": 'Invalid JSON payload received. Unknown name "voice"'}})
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        text = body["contents"][0]["parts"][0]["text"]
        seconds = 0.2 if self.short else max(0.5, len(text) / 14)
        if self.pcm:
            pcm = (np.zeros(int(seconds * 24000), dtype="<i2")).tobytes()
            inline = {"mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(pcm).decode()}
        else:
            inline = {"mimeType": "audio/wav", "data": _wav_b64(seconds)}
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": inline}]}}]})


def _client(fake):
    return GeminiClient("good-key", transport=httpx.MockTransport(fake))


# ------------------------------------------------------------------------------- client

async def test_request_shape_and_wav_decoding():
    fake = FakeGoogle()
    audio = await _client(fake).synthesize("Привет, мир.", voice="Kore", style="calm", model="gemini-3.8-flash-tts")
    assert audio.dtype == np.float32 and len(audio) > 0
    part = fake.bodies[0]["contents"][0]["parts"][0]
    assert part["speech_metadata"] == {"style": "calm"}
    assert fake.bodies[0]["generationConfig"]["speechConfig"]["voiceConfig"] == {"voice": "Kore"}
    assert fake.bodies[0]["generationConfig"]["responseModalities"] == ["AUDIO"]


async def test_headerless_pcm_decoding():
    audio = await _client(FakeGoogle(pcm=True)).synthesize("Hello there.", voice="Kore", style="", model="gemini-3.8-flash-tts")
    assert len(audio) > 0


async def test_falls_back_to_legacy_request_dialect_and_remembers_it():
    fake = FakeGoogle(legacy_only=True)
    client = _client(fake)
    await client.synthesize("Один.", voice="Kore", style="calm", model="gemini-3.8-flash-tts")
    await client.synthesize("Два.", voice="Kore", style="calm", model="gemini-3.8-flash-tts")
    second = fake.bodies[-1]
    assert second["generationConfig"]["speechConfig"]["voiceConfig"] == {"prebuiltVoiceConfig": {"voiceName": "Kore"}}
    assert second["contents"][0]["parts"][0]["speech_metadata"] == {"style": "calm"}
    assert len(fake.bodies) == 3  # 1 rejected + 1 accepted + 1 accepted straight away


async def test_rate_limit_is_retried():
    fake = FakeGoogle(rate_limit_first=2)
    await _client(fake).synthesize("Текст.", voice="Kore", style="", model="gemini-3.8-flash-tts")
    assert len(fake.bodies) == 3


async def test_bad_key_is_an_auth_error():
    with pytest.raises(GeminiAuthError):
        await GeminiClient("wrong", transport=httpx.MockTransport(FakeGoogle())).synthesize(
            "x", voice="Kore", style="", model="gemini-3.8-flash-tts")


# ------------------------------------------------------------------------------- worker

def _items(tmp_path, texts):
    return [SynthesisItem(segment_id=f"s{i}", text=t, lang=Lang.ru, out_wav=str(tmp_path / f"s{i}.wav"))
            for i, t in enumerate(texts)]


async def test_worker_runs_requests_concurrently_and_writes_wavs(tmp_path):
    fake = FakeGoogle()
    w = GeminiWorker(key_fn=lambda: "good-key", transport=httpx.MockTransport(fake))
    res = await w.synthesize(_items(tmp_path, [f"Предложение номер {i}." for i in range(8)]),
                             gemini_voice("Kore", Lang.ru, "gemini-3.8-flash-tts"), SynthesisParams(), qa=False)
    assert all(r.ok for r in res) and fake.max_in_flight > 1
    assert fake.max_in_flight <= config.GEMINI_CONCURRENCY
    assert sf.info(res[0].out_wav).samplerate == 24000


async def test_worker_retries_implausibly_short_audio(tmp_path):
    fake = FakeGoogle(short=True)
    w = GeminiWorker(key_fn=lambda: "good-key", transport=httpx.MockTransport(fake))
    res = await w.synthesize(_items(tmp_path, ["Длинное предложение, которое точно звучит дольше секунды."]),
                             gemini_voice("Kore", Lang.ru, "gemini-3.8-flash-tts"), SynthesisParams(), qa=False)
    assert res[0].ok and res[0].attempts == 2 and len(fake.bodies) == 2


async def test_worker_auth_error_raises_for_the_scheduler(tmp_path):
    w = GeminiWorker(key_fn=lambda: "wrong", transport=httpx.MockTransport(FakeGoogle()))
    with pytest.raises(RuntimeError, match="API key"):
        await w.synthesize(_items(tmp_path, ["Текст."]), gemini_voice("Kore", Lang.ru, "m"),
                           SynthesisParams(), qa=False)


# ------------------------------------------------------------------------------- scheduler e2e

async def test_job_with_gemini_engine_completes_without_touching_local_worker(scheduler_factory, sample_book_txt):
    fake = FakeGoogle()
    sch = scheduler_factory(FakeWorker())
    sch.cloud_worker = GeminiWorker(key_fn=lambda: "good-key", transport=httpx.MockTransport(fake))
    await sch.start()
    try:
        info = await sch.create_job(sample_book_txt, "sample_book.txt")
        sch.update_settings(info.id, JobSettings(engine=EngineName.gemini, gemini_voice="Puck",
                                                 gemini_style="весело"))
        await sch.start_job(info.id)
        for _ in range(200):
            if sch.get_job(info.id).status == JobStatus.done:
                break
            await asyncio.sleep(0.05)
        assert sch.get_job(info.id).status == JobStatus.done
        assert sch.worker.calls == 0 and fake.bodies
        assert {b["generationConfig"]["speechConfig"]["voiceConfig"]["voice"] for b in fake.bodies} == {"Puck"}
    finally:
        await sch.stop()


# ------------------------------------------------------------------------------- API + key storage

@pytest.fixture
def api(fake_services, tmp_path):
    fake = FakeGoogle()
    app = create_app(services=fake_services, worker=FakeWorker(), data_dir=tmp_path / "data",
                     allowed_hosts=["127.0.0.1", "localhost", "testserver"],
                     cloud_worker=GeminiWorker(transport=httpx.MockTransport(fake)))
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        os.environ.pop(var, None)
    with TestClient(app) as c:
        yield c, fake


def test_key_is_verified_stored_privately_and_never_returned(api):
    c, _ = api
    assert c.put("/api/keys/gemini", json={"api_key": "wrong"}).status_code == 400
    assert c.get("/api/keys/gemini").json()["configured"] is False
    r = c.put("/api/keys/gemini", json={"api_key": "good-key"})
    assert r.status_code == 200
    assert r.json() == {"configured": True, "last4": "-key", "source": "file"}
    path = config.DATA_DIR / "secrets.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for url in ("/api/keys/gemini", "/api/engines", "/api/system"):
        assert "good-key" not in c.get(url).text
    assert c.delete("/api/keys/gemini").status_code == 204
    assert keystore.gemini_key() is None


def test_voice_preview_is_cached(api):
    c, fake = api
    c.put("/api/keys/gemini", json={"api_key": "good-key"})
    body = {"voice": "Charon", "lang": "ru"}
    r1 = c.post("/api/engines/gemini/preview", json=body)
    r2 = c.post("/api/engines/gemini/preview", json=body)
    assert r1.status_code == r2.status_code == 200 and r1.headers["content-type"] == "audio/wav"
    assert len(fake.bodies) == 1
    assert c.post("/api/engines/gemini/preview", json={"voice": "Nobody"}).status_code == 404


def test_engines_listing(api):
    c, _ = api
    eng = {e["id"]: e for e in c.get("/api/engines").json()}
    assert eng["local"]["offline"] and not eng["gemini"]["offline"]
    assert len(eng["gemini"]["voices"]) == 30 and eng["gemini"]["default_model"] == config.GEMINI_MODEL
