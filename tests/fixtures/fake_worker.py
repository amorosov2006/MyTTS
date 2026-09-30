"""FakeWorker: a TTSWorker for scheduler/API tests. Writes real (tiny) tone wavs so the
downstream fake post-processing has real audio to work with, and can be configured to fail
specific segments or crash on a given call to exercise retry/pause paths."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterable

import numpy as np
import soundfile as sf

from mytts.config import SAMPLE_RATE
from mytts.contracts import (
    SynthesisItem, SynthesisResult, SynthesisParams, Voice, WorkerCrashed, WorkerStatus,
)

CHARS_PER_S = 14.0


class FakeWorker:
    def __init__(self, *, delay: float = 0.0, fail_ids: Iterable[str] = (),
                 crash_on_calls: Iterable[int] = ()):
        self.delay = delay
        self.fail_ids = set(fail_ids)
        self.crash_on_calls = set(crash_on_calls)
        self.calls = 0
        self.started = False
        self.design_calls: list[tuple] = []
        self.synthesize_batches: list[list[str]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    def status(self) -> WorkerStatus:
        return WorkerStatus(state="idle" if self.started else "stopped", footprint_gb=1.0,
                            system_available_gb=20.0, restarts=0, model="fake")

    async def synthesize(self, items: list[SynthesisItem], voice: Voice, params: SynthesisParams,
                         qa: bool) -> list[SynthesisResult]:
        self.calls += 1
        self.synthesize_batches.append([i.segment_id for i in items])
        if self.calls in self.crash_on_calls:
            raise WorkerCrashed()
        if self.delay:
            await asyncio.sleep(self.delay)
        results = []
        for item in items:
            if item.segment_id in self.fail_ids:
                results.append(SynthesisResult(segment_id=item.segment_id, ok=False,
                                               error="fake synthesis failure"))
                continue
            seconds = max(0.3, len(item.text) / CHARS_PER_S)
            n = int(seconds * SAMPLE_RATE)
            tone = 0.1 * np.sin(2 * np.pi * 220 * np.arange(n) / SAMPLE_RATE)
            pad = np.zeros(int(0.1 * SAMPLE_RATE))
            audio = np.concatenate([pad, tone, pad]).astype(np.float32)
            Path(item.out_wav).parent.mkdir(parents=True, exist_ok=True)
            sf.write(item.out_wav, audio, SAMPLE_RATE)
            results.append(SynthesisResult(segment_id=item.segment_id, ok=True, out_wav=item.out_wav,
                                           duration_s=len(audio) / SAMPLE_RATE,
                                           cer=0.0 if qa else None,
                                           transcript=item.text if qa else None))
        return results

    async def design_voice(self, description: str, lang, text: str, out_wav: str) -> float:
        self.design_calls.append((description, lang, text, out_wav))
        seconds = max(0.5, len(text) / CHARS_PER_S)
        n = int(seconds * SAMPLE_RATE)
        tone = 0.1 * np.sin(2 * np.pi * 300 * np.arange(n) / SAMPLE_RATE).astype(np.float32)
        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
        sf.write(out_wav, tone, SAMPLE_RATE)
        return seconds
