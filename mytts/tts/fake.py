"""Deterministic fake engine + verifier for tests: no GPU, no models, milliseconds per call."""
from typing import Sequence

import numpy as np

from mytts.config import SAMPLE_RATE, token_budget
from mytts.contracts import EngineOutput, Lang, SynthesisParams, Voice

CHARS_PER_SECOND = 14.0


class FakeEngine:
    sample_rate = SAMPLE_RATE

    def __init__(self, fail_texts: Sequence[str] = (), cap_texts: Sequence[str] = ()):
        self.fail_texts, self.cap_texts = set(fail_texts), set(cap_texts)
        self.loaded = False
        self.calls: list[list[str]] = []

    def load(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        self.loaded = False

    def synthesize(self, texts: Sequence[str], lang: Lang, voice: Voice,
                   params: SynthesisParams) -> list[EngineOutput]:
        assert self.loaded, "load() first"
        self.calls.append(list(texts))
        out = []
        for t in texts:
            if t in self.fail_texts:
                raise RuntimeError(f"fake failure for {t!r}")
            seconds = max(0.3, len(t) / CHARS_PER_SECOND)
            n = int(seconds * self.sample_rate)
            tone = 0.1 * np.sin(2 * np.pi * 220 * np.arange(n) / self.sample_rate)
            pad = np.zeros(int(0.15 * self.sample_rate))  # leading/trailing silence to trim
            audio = np.concatenate([pad, tone, pad]).astype(np.float32)
            capped = t in self.cap_texts
            out.append(EngineOutput(audio=audio, tokens=token_budget(t) if capped else int(seconds * 12.5),
                                    hit_token_cap=capped))
        return out


class FakeVerifier:
    def __init__(self, bad_texts: Sequence[str] = ()):
        self.bad_texts = set(bad_texts)

    def check(self, audio: np.ndarray, sample_rate: int, text: str, lang: Lang) -> tuple[float, str]:
        return (0.5, "garbled") if text in self.bad_texts else (0.0, text)
