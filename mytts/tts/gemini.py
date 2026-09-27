"""Google Gemini TTS client (optional cloud engine).

The ONLY module in MyTTS that talks to the network, and only when a job/preview selects the
Gemini engine. REST `models/{model}:generateContent` (docs: ai.google.dev/gemini-api/docs/
generate-content/speech-generation). Two request dialects exist:
  - 3.x models: parts[].speech_metadata.style + speechConfig.voiceConfig.voice
  - 2.5 preview: style as a prompt prefix + speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName
We send the 3.x form and fall back to the 2.5 form once if the API rejects the fields, then
remember which one works per model. Audio comes back as WAV or headerless 16-bit PCM, 24 kHz.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import random

import httpx
import numpy as np
import soundfile as sf

from mytts import config

log = logging.getLogger("mytts.gemini")

# name -> (Google's one-word style, gender as commonly described)
VOICES: dict[str, tuple[str, str]] = {
    "Zephyr": ("Bright", "female"), "Puck": ("Upbeat", "male"), "Charon": ("Informative", "male"),
    "Kore": ("Firm", "female"), "Fenrir": ("Excitable", "male"), "Leda": ("Youthful", "female"),
    "Orus": ("Firm", "male"), "Aoede": ("Breezy", "female"), "Callirrhoe": ("Easy-going", "female"),
    "Autonoe": ("Bright", "female"), "Enceladus": ("Breathy", "male"), "Iapetus": ("Clear", "male"),
    "Umbriel": ("Easy-going", "male"), "Algieba": ("Smooth", "male"), "Despina": ("Smooth", "female"),
    "Erinome": ("Clear", "female"), "Algenib": ("Gravelly", "male"), "Rasalgethi": ("Informative", "male"),
    "Laomedeia": ("Upbeat", "female"), "Achernar": ("Soft", "female"), "Alnilam": ("Firm", "male"),
    "Schedar": ("Even", "male"), "Gacrux": ("Mature", "female"), "Pulcherrima": ("Forward", "female"),
    "Achird": ("Friendly", "male"), "Zubenelgenubi": ("Casual", "male"),
    "Vindemiatrix": ("Gentle", "female"), "Sadachbia": ("Lively", "male"),
    "Sadaltager": ("Knowledgeable", "male"), "Sulafat": ("Warm", "female"),
}


class GeminiError(Exception):
    """Any Gemini failure; message is safe to show to the user (never contains the key)."""


class GeminiAuthError(GeminiError):
    """Key missing/invalid/unauthorized or billing not enabled — retrying won't help."""


class GeminiClient:
    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        if not api_key:
            raise GeminiAuthError("No Gemini API key configured (Settings → Google Gemini).")
        self._client = httpx.AsyncClient(
            base_url=config.GEMINI_API_BASE, timeout=config.GEMINI_TIMEOUT_S, transport=transport,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"})
        self._legacy: dict[str, bool] = {}  # model -> needs the 2.5-style request

    async def aclose(self) -> None:
        await self._client.aclose()

    async def check_key(self) -> None:
        """Cheap authenticated call (list models). Raises GeminiAuthError if the key is bad."""
        r = await self._client.get("/models", params={"pageSize": 1})
        if r.status_code != 200:
            raise _error_from(r)

    async def synthesize(self, text: str, *, voice: str, style: str, model: str) -> np.ndarray:
        """float32 mono audio at config.SAMPLE_RATE (24 kHz)."""
        legacy = self._legacy.get(model, model.startswith("gemini-2.5"))
        switched = False
        for attempt in range(config.GEMINI_MAX_RETRIES + 1):
            r = await self._client.post(f"/models/{model}:generateContent",
                                        json=_body(text, voice, style, legacy))
            if r.status_code == 200:
                self._legacy[model] = legacy  # remember the dialect that works
                return _decode(r.json())
            if r.status_code == 400 and not switched and model not in self._legacy and _unknown_field(r):
                legacy, switched = not legacy, True  # try the other request dialect once
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < config.GEMINI_MAX_RETRIES:
                delay = _retry_delay(r, attempt)
                log.warning("Gemini %s, retrying in %.1fs", r.status_code, delay)
                await asyncio.sleep(delay)
                continue
            raise _error_from(r)
        raise GeminiError("Gemini TTS failed after retries")


def _body(text: str, voice: str, style: str, legacy: bool) -> dict:
    if legacy:
        prompt = f"{style.rstrip('.:')}:\n{text}" if style else text
        return {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    part: dict = {"text": text}
    if style:
        part["speech_metadata"] = {"style": style}
    return {"contents": [{"role": "user", "parts": [part]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"voice": voice}}}}


def _decode(payload: dict) -> np.ndarray:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        inline = next(p.get("inlineData") or p.get("inline_data") for p in parts
                      if p.get("inlineData") or p.get("inline_data"))
    except (KeyError, IndexError, StopIteration, TypeError):
        reason = (payload.get("candidates") or [{}])[0].get("finishReason") or \
            (payload.get("promptFeedback") or {}).get("blockReason")
        raise GeminiError(f"Gemini returned no audio ({reason or 'empty response'})")
    raw = base64.b64decode(inline["data"])
    if raw[:4] == b"RIFF":
        audio, sr = sf.read(io.BytesIO(raw), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
    else:  # headerless 16-bit little-endian PCM (audio/l16 or audio/pcm)
        mime = inline.get("mimeType") or inline.get("mime_type") or ""
        sr = int(mime.split("rate=")[1].split(";")[0]) if "rate=" in mime else config.SAMPLE_RATE
        audio = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    if sr != config.SAMPLE_RATE:
        from scipy.signal import resample_poly
        audio = resample_poly(audio, config.SAMPLE_RATE, sr).astype(np.float32)
    return np.ascontiguousarray(audio, dtype=np.float32)


def _message(r: httpx.Response) -> str:
    try:
        return (r.json().get("error") or {}).get("message") or r.text[:300]
    except ValueError:
        return r.text[:300]


def _unknown_field(r: httpx.Response) -> bool:
    msg = _message(r).lower()
    return "unknown name" in msg or "invalid json payload" in msg or "cannot find field" in msg


def _retry_delay(r: httpx.Response, attempt: int) -> float:
    if r.headers.get("retry-after", "").isdigit():
        return float(r.headers["retry-after"])
    try:  # google.rpc.RetryInfo in error.details, e.g. {"retryDelay": "17s"}
        for d in (r.json().get("error") or {}).get("details", []):
            if "retryDelay" in d:
                return float(d["retryDelay"].rstrip("s")) + 0.5
    except (ValueError, AttributeError):
        pass
    return min(60.0, 2 ** attempt + random.random())


def _error_from(r: httpx.Response) -> GeminiError:
    msg = _message(r)
    if r.status_code in (401, 403) or "api key" in msg.lower() or "API_KEY" in msg:
        return GeminiAuthError(f"Gemini rejected the API key ({r.status_code}): {msg}")
    if r.status_code == 429:
        return GeminiError(f"Gemini rate limit / quota exceeded: {msg}")
    if "billing" in msg.lower() or "free tier" in msg.lower():
        return GeminiAuthError(f"Gemini: {msg}")
    return GeminiError(f"Gemini error {r.status_code}: {msg}")
