"""Qwen3-TTS (mlx-audio, Apple GPU/MLX) implementing contracts.Engine, plus VoiceDesigner for
one-time narrator design.

Real-model code: only run under bench/memguard.py, one process at a time (CLAUDE.md, PLAN.md
"Memory safety"). Never import/instantiate outside a worker process.

Reference-audio caching: mlx_audio's Qwen3TTS model caches the ICL reference encoding
(speech-tokenizer codes + tokenized ref text) on the model instance itself, keyed by
`(ref_text, ref_audio fingerprint)` -- see `Qwen3TTSTalkerForConditionalGeneration._icl_cache`
in `mlx_audio/tts/models/qwen3_tts/qwen3_tts.py` (`_prepare_icl_reference`). So passing
`ref_audio` as a path re-reads/re-encodes the reference only the FIRST time a given voice is
used against a loaded model; later calls with the same (ref_text, ref_audio) hit that cache.
No extra caching is needed here -- just keep the model resident (load() once, reuse for the
whole book) rather than reconstructing QwenEngine per call.
"""
from __future__ import annotations

from typing import Sequence

import mlx.core as mx
import numpy as np

from mytts import config
from mytts.contracts import EngineOutput, Lang, SynthesisParams, Voice

_LANG_CODE = {Lang.ru: "russian", Lang.en: "english"}


def _set_mlx_limits() -> None:
    mx.set_memory_limit(int(config.MLX_MEMORY_LIMIT_GB * 1e9))
    mx.set_cache_limit(int(config.MLX_CACHE_LIMIT_GB * 1e9))


class QwenEngine:
    """Base (voice-cloning) Qwen3-TTS model."""

    sample_rate = config.SAMPLE_RATE

    def __init__(self, model_id: str = config.TTS_MODEL):
        self.model_id = model_id
        self._model = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        from mlx_audio.tts.utils import load_model

        _set_mlx_limits()
        self._model = load_model(self.model_id)

    def unload(self) -> None:
        self._model = None
        mx.clear_cache()

    def synthesize(self, texts: Sequence[str], lang: Lang, voice: Voice,
                   params: SynthesisParams) -> list[EngineOutput]:
        assert self._model is not None, "load() first"
        texts = list(texts)
        lang_code = _LANG_CODE[lang]
        budgets = [config.token_budget(t) for t in texts]

        if len(texts) == 1:
            budget = budgets[0]
            results = list(self._model.generate(
                text=texts[0], ref_audio=voice.ref_audio, ref_text=voice.ref_text,
                lang_code=lang_code, split_pattern="", max_tokens=budget,
                temperature=params.temperature, top_p=params.top_p,
                repetition_penalty=params.repetition_penalty,
            ))
            audio = (np.concatenate([np.array(r.audio) for r in results]).astype(np.float32)
                     if results else np.zeros(0, dtype=np.float32))
            tokens = sum(r.token_count for r in results)
            mx.clear_cache()
            return [EngineOutput(audio=audio, tokens=tokens, hit_token_cap=tokens >= budget - 1)]

        # Batched: mlx_audio caps the whole batch at one shared max_tokens.
        max_budget = max(budgets)
        chunks: list[list[np.ndarray]] = [[] for _ in texts]
        token_counts = [0] * len(texts)
        for r in self._model.batch_generate(
            texts=texts, ref_audio=voice.ref_audio, ref_text=voice.ref_text,
            lang_code=lang_code, stream=False, max_tokens=max_budget,
            temperature=params.temperature, top_p=params.top_p,
            repetition_penalty=params.repetition_penalty,
        ):
            chunks[r.sequence_idx].append(np.array(r.audio))
            token_counts[r.sequence_idx] += r.token_count
        mx.clear_cache()

        outputs = []
        for i in range(len(texts)):
            audio = (np.concatenate(chunks[i]).astype(np.float32) if chunks[i]
                     else np.zeros(0, dtype=np.float32))
            outputs.append(EngineOutput(audio=audio, tokens=token_counts[i],
                                         hit_token_cap=token_counts[i] >= max_budget - 1))
        return outputs


class VoiceDesigner:
    """VoiceDesign model: builds a narrator clip from a text description. Used once per voice;
    never resident at the same time as the Base engine (worker.py unloads Base first)."""

    sample_rate = config.SAMPLE_RATE

    def __init__(self, model_id: str = config.VOICE_DESIGN_MODEL):
        self.model_id = model_id
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        from mlx_audio.tts.utils import load_model

        _set_mlx_limits()
        self._model = load_model(self.model_id)

    def unload(self) -> None:
        self._model = None
        mx.clear_cache()

    def generate_voice_design(self, text: str, instruct: str, language: str) -> np.ndarray:
        assert self._model is not None, "load() first"
        results = list(self._model.generate_voice_design(text=text, instruct=instruct, language=language))
        audio = (np.concatenate([np.array(r.audio) for r in results]).astype(np.float32)
                 if results else np.zeros(0, dtype=np.float32))
        mx.clear_cache()
        return audio
