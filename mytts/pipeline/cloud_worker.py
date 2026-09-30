"""TTSWorker for Google Gemini TTS (cloud). Implements contracts.TTSWorker.

No GPU, negligible memory: segments of a batch are sent concurrently (bounded by
config.GEMINI_CONCURRENCY). QA here is a duration sanity check (a truncated or runaway
generation has an implausible length for its text) with one retry; Whisper QA stays on the
local engine. A rejected key / missing billing raises, so the scheduler pauses the job with
that message instead of burning through every segment.
"""
from __future__ import annotations

import asyncio
import os
from typing import Callable, Optional

import soundfile as sf

from mytts import config
from mytts.contracts import Lang, SynthesisItem, SynthesisParams, SynthesisResult, Voice, WorkerStatus
from mytts.tts.gemini import ApiKeyAuth, GeminiClient, GeminiPause, current_auth

CHARS_PER_SECOND = 14.0


def plausible_duration(text: str, seconds: float) -> bool:
    expected = max(1.0, len(text) / CHARS_PER_SECOND)
    return expected * 0.35 <= seconds <= expected * 2.8 + 2.0


class GeminiWorker:
    def __init__(self, key_fn: Optional[Callable[[], Optional[str]]] = None, transport=None,
                 auth_fn: Optional[Callable] = None):
        self._auth_fn = auth_fn or ((lambda: ApiKeyAuth(key_fn() or "")) if key_fn else current_auth)
        self._transport = transport  # tests inject httpx.MockTransport
        self._client: Optional[GeminiClient] = None
        self._client_key: Optional[str] = None
        self._busy = 0
        self._message: Optional[str] = None
        self.requests = 0
        self.retry_at: Optional[float] = None       # when a paused job may continue (quota reset)
        self.pause_reason: Optional[str] = None     # set when the last batch hit a job-level stop

    def _get_client(self) -> GeminiClient:
        auth = self._auth_fn()
        ident = getattr(auth, "_key", None)
        if self._client is None or ident != self._client_key:
            old = self._client
            self._client = GeminiClient(auth, transport=self._transport)
            self._client_key = ident
            if old is not None:  # key changed: close the old connection pool once idle
                asyncio.get_running_loop().call_later(
                    config.GEMINI_TIMEOUT_S, lambda: asyncio.ensure_future(old.aclose()))
        return self._client

    @staticmethod
    def resolve_model(model: str) -> str:
        """The job's model if still offered (3.8 only), else the default 3.8 Flash TTS."""
        return model if model in config.GEMINI_MODELS else config.GEMINI_MODEL

    async def check_connection(self) -> None:
        await self._get_client().check_key()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def status(self) -> WorkerStatus:
        return WorkerStatus(state="busy" if self._busy else "idle", model="gemini",
                            message=self._message)

    async def check_key(self, key: str) -> None:
        client = GeminiClient(key, transport=self._transport)
        try:
            await client.check_key()
        finally:
            await client.aclose()

    async def synthesize(self, items: list[SynthesisItem], voice: Voice, params: SynthesisParams,
                         qa: bool) -> list[SynthesisResult]:
        """voice.id is "gemini:<VoiceName>"; voice.description carries the model id.
        A job-level problem (bad key, empty balance, daily quota, Google unreachable) never
        discards finished audio: completed items are returned ok, the rest retry_later=True,
        and self.pause_reason / self.retry_at say why and until when."""
        client = self._get_client()
        model = self.resolve_model(voice.description)
        name = voice.id.split(":", 1)[-1]
        sem = asyncio.Semaphore(config.GEMINI_CONCURRENCY)
        stop: list[GeminiPause] = []

        def later(it: SynthesisItem) -> SynthesisResult:
            return SynthesisResult(segment_id=it.segment_id, ok=False, retry_later=True,
                                   error=str(stop[0]) if stop else None)

        async def one(it: SynthesisItem) -> SynthesisResult:
            async with sem:
                if stop:
                    return later(it)  # don't start new billable calls after a stop
                best, last_err, attempt = None, None, 0
                for attempt in (1, 2):
                    try:
                        self.requests += 1
                        audio = await client.synthesize(it.text, voice=name, style=params.instruction,
                                                        model=model)
                    except GeminiPause as e:
                        stop.append(e)
                        return later(it)
                    except Exception as e:  # this text's result was bad: one more try
                        last_err = str(e)
                        continue
                    best = audio
                    if plausible_duration(it.text, len(audio) / config.SAMPLE_RATE):
                        break
                if best is None:
                    return SynthesisResult(segment_id=it.segment_id, ok=False, attempts=attempt, error=last_err)
                tmp = f"{it.out_wav}.part.wav"
                await asyncio.to_thread(sf.write, tmp, best, config.SAMPLE_RATE, subtype="PCM_16")
                os.replace(tmp, it.out_wav)
                return SynthesisResult(segment_id=it.segment_id, ok=True, out_wav=it.out_wav,
                                       duration_s=len(best) / config.SAMPLE_RATE, attempts=attempt)

        self._busy += 1
        try:
            results = await asyncio.gather(*(one(it) for it in items), return_exceptions=True)
        finally:
            self._busy -= 1
        results = [r if isinstance(r, SynthesisResult) else
                   SynthesisResult(segment_id=it.segment_id, ok=False, error=f"{type(r).__name__}: {r}")
                   for it, r in zip(items, results)]
        self.pause_reason = str(stop[0]) if stop else None
        self.retry_at = stop[0].retry_at if stop else None
        self._message = self.pause_reason
        return results

    async def design_voice(self, description: str, lang: Lang, text: str, out_wav: str) -> float:
        raise NotImplementedError("Voice design runs on the local engine")


def gemini_voice(name: str, lang: Lang, model: str) -> Voice:
    """A contracts.Voice standing for a prebuilt Gemini voice (no reference audio)."""
    return Voice(id=f"gemini:{name}", name=name, lang=lang, ref_audio="", ref_text="",
                 description=model)
