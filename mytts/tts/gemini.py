"""Google Gemini TTS client (optional cloud engine).

The ONLY module in MyTTS that talks to the network, and only when a job/preview selects the
Gemini engine. REST `models/{model}:generateContent` (docs: ai.google.dev/gemini-api/docs/
generate-content/speech-generation). Two request dialects exist:
  - documented 3.8 form: parts[].speech_metadata.style + speechConfig.voiceConfig.voice
  - older field names: speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName (+ prompt-prefix style)
We send the 3.8 form and fall back only if the API rejects the fields, then remember which
one works per model. Only the Gemini 3.8 TTS models are offered (config.GEMINI_MODELS). Audio comes back as WAV or headerless 16-bit PCM, 24 kHz.
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


class GeminiPause(GeminiError):
    """Stop the whole job (not just one segment): retrying the next segment won't help now.
    retry_at: epoch seconds when it may work again (None = needs the user)."""
    retry_at: float | None = None

    def __init__(self, message: str, retry_at: float | None = None):
        super().__init__(message)
        self.retry_at = retry_at


class GeminiAuthError(GeminiPause):
    """Key missing/invalid/unauthorized, billing not enabled or prepaid balance empty."""


class GeminiQuotaError(GeminiPause):
    """Daily quota exhausted; retry_at = when Google says requests work again."""


class GeminiUnavailable(GeminiPause):
    """Network down / Google failing or rate-limiting beyond our retries."""


class ApiKeyAuth:
    """Google AI Studio API key -> generativelanguage.googleapis.com."""
    method = "api_key"
    base_url = config.GEMINI_API_BASE

    def __init__(self, api_key: str):
        if not api_key:
            raise GeminiAuthError("Google Gemini is not connected (⚙ → Cloud engines).")
        self._key = api_key

    async def headers(self) -> dict:
        return {"x-goog-api-key": self._key}

    def model_path(self, model: str) -> str:
        return f"/models/{model}:generateContent"

    def check_path(self) -> str:
        return "/models"


def current_auth() -> ApiKeyAuth:
    from mytts import keystore
    return ApiKeyAuth(keystore.gemini_key() or "")


def connection_status() -> dict:
    """What the UI shows about the key (never the key itself). No network."""
    from mytts import keystore
    return keystore.gemini_key_status()


# Request dialects, tried in order until one is accepted (then remembered per model): the
# documented 3.8 form first; the older field names only if the API rejects it.
DIALECTS = [("voice", "metadata"), ("prebuilt", "metadata"), ("prebuilt", "prefix")]


class RateLimiter:
    """Spaces out request starts and adapts to the account's per-minute quota: halve the rate on
    a 429, speed up by 25% after every 10 successes (bounded)."""

    def __init__(self, rpm: float = config.GEMINI_START_RPM):
        self.rpm = rpm
        self._next = 0.0
        self._ok = 0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            start = max(loop.time(), self._next)
            self._next = start + 60.0 / self.rpm
        await asyncio.sleep(max(0.0, start - loop.time()))

    def on_success(self) -> None:
        self._ok += 1
        if self._ok >= 10:
            self._ok = 0
            self.rpm = min(config.GEMINI_MAX_RPM, self.rpm * 1.25)

    def on_rate_limited(self) -> None:
        self._ok = 0
        self.rpm = max(config.GEMINI_MIN_RPM, self.rpm / 2)


class GeminiClient:
    def __init__(self, auth, *, transport: httpx.AsyncBaseTransport | None = None):
        if isinstance(auth, str) or auth is None:
            auth = ApiKeyAuth(auth or "")
        self.auth = auth
        self._client = httpx.AsyncClient(base_url=auth.base_url, timeout=config.GEMINI_TIMEOUT_S,
                                         transport=transport, headers={"Content-Type": "application/json"})
        self._dialect: dict[str, int] = {}  # model -> index into DIALECTS known to work
        self.limiter = RateLimiter()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def check_key(self) -> None:
        """Cheap authenticated call. Raises GeminiAuthError if the credentials are rejected."""
        r = await self._client.get(self.auth.check_path(), headers=await self.auth.headers())
        if r.status_code != 200:
            raise _error_from(r)

    async def synthesize(self, text: str, *, voice: str, style: str, model: str) -> np.ndarray:
        """float32 mono audio at config.SAMPLE_RATE (24 kHz). Raises GeminiPause subclasses for
        job-level problems, plain GeminiError for a bad result of this one text."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + config.GEMINI_CALL_DEADLINE_S
        known = model in self._dialect
        d = self._dialect.get(model, 0)
        last = "no response"
        for attempt in range(config.GEMINI_MAX_RETRIES + len(DIALECTS)):
            if loop.time() > deadline:
                break
            await self.limiter.wait()
            try:
                r = await self._client.post(self.auth.model_path(model), headers=await self.auth.headers(),
                                            json=_body(text, voice, style, *DIALECTS[d]))
            except httpx.TransportError as e:  # network blip, DNS, timeout
                last = f"network error: {type(e).__name__}: {e}"
                delay = min(config.GEMINI_MAX_WAIT_S, 2 ** attempt + random.random())
                log.warning("Gemini %s, retrying in %.1fs", last, delay)
                await asyncio.sleep(delay)
                continue
            if r.status_code == 200:
                self._dialect[model] = d
                self.limiter.on_success()
                try:
                    return _decode(r.json())
                except GeminiError:
                    raise
                except Exception as e:  # malformed JSON / undecodable audio
                    raise GeminiError(f"Gemini returned unreadable audio: {e}") from e
            if r.status_code == 400 and not known and d + 1 < len(DIALECTS) and not _is_auth(r):
                d += 1  # field names rejected: try the next dialect
                continue
            if r.status_code == 429 and _is_daily_quota(r):
                raise _quota_error(r, _retry_delay(r, attempt, default=3600.0))
            if r.status_code in (429, 500, 502, 503, 504):
                delay = _retry_delay(r, attempt)
                if r.status_code == 429:
                    if delay > config.GEMINI_MAX_WAIT_S:
                        raise _quota_error(r, delay)
                    self.limiter.on_rate_limited()
                delay = min(delay, config.GEMINI_MAX_WAIT_S)
                last = f"{r.status_code}: {_message(r)[:200]}"
                log.warning("Gemini %s, retrying in %.1fs (pace now %.1f req/min)", last, delay,
                            self.limiter.rpm)
                await asyncio.sleep(delay)
                continue
            raise _error_from(r)
        import time
        raise GeminiUnavailable(f"Google Gemini is not responding normally ({last}). The job is paused "
                                "and continues automatically in a few minutes.",
                                time.time() + config.GEMINI_MAX_WAIT_S)


def _body(text: str, voice: str, style: str, voice_form: str, style_form: str) -> dict:
    part: dict = {"text": text}
    if style and style_form == "prefix":
        part["text"] = f"{style.rstrip('.:')}:\n{text}"
    elif style:
        part["speech_metadata"] = {"style": style}
    voice_cfg = {"voice": voice} if voice_form == "voice" else {"prebuiltVoiceConfig": {"voiceName": voice}}
    return {"contents": [{"role": "user", "parts": [part]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": voice_cfg}}}


def _decode(payload: dict) -> np.ndarray:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        inline = next(p.get("inlineData") or p.get("inline_data") for p in parts
                      if p.get("inlineData") or p.get("inline_data"))
    except (KeyError, IndexError, StopIteration, TypeError) as e:
        reason = (payload.get("candidates") or [{}])[0].get("finishReason") or \
            (payload.get("promptFeedback") or {}).get("blockReason")
        raise GeminiError(f"Gemini returned no audio ({reason or 'empty response'})") from e
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


def _is_auth(r: httpx.Response) -> bool:
    msg = _message(r)
    return r.status_code in (401, 403) or "api key" in msg.lower() or "API_KEY" in msg


def _is_daily_quota(r: httpx.Response) -> bool:
    try:
        for d in (r.json().get("error") or {}).get("details", []):
            for v in d.get("violations", []):
                if "PerDay" in (v.get("quotaId") or "") or "per_day" in (v.get("quotaMetric") or ""):
                    return True
    except (ValueError, AttributeError):
        pass
    return False


def _retry_delay(r: httpx.Response, attempt: int, default: float | None = None) -> float:
    if r.headers.get("retry-after", "").isdigit():
        return float(r.headers["retry-after"])
    try:  # google.rpc.RetryInfo in error.details, e.g. {"retryDelay": "17s"}
        for d in (r.json().get("error") or {}).get("details", []):
            if "retryDelay" in d:
                return float(d["retryDelay"].rstrip("s")) + 0.5
    except (ValueError, AttributeError):
        pass
    return default if default is not None else min(60.0, 2 ** attempt + random.random())


def _quota_error(r: httpx.Response, delay: float) -> GeminiQuotaError:
    import time
    from datetime import datetime
    limit = ""
    try:
        for d in (r.json().get("error") or {}).get("details", []):
            for v in d.get("violations", []):
                if v.get("quotaValue"):
                    limit = f" ({v['quotaValue']} requests/day for this model)"
    except (ValueError, AttributeError):
        pass
    at = time.time() + delay
    when = datetime.fromtimestamp(at).strftime("%H:%M")
    return GeminiQuotaError(f"Google Gemini daily quota reached{limit}. MyTTS continues automatically "
                            f"at {when} while the app is running (or press Resume after {when}).", at)


def _error_from(r: httpx.Response) -> GeminiError:
    msg = _message(r)
    if r.status_code == 402 or "prepay" in msg.lower():
        return GeminiAuthError("Google Gemini prepaid balance is empty — add credits at "
                               "https://aistudio.google.com/billing (Buy credits), then press Resume. "
                               f"Google said: {msg}")
    if _is_auth(r):
        return GeminiAuthError(f"Gemini rejected the API key ({r.status_code}): {msg}")
    if r.status_code == 404:
        return GeminiError(f"Gemini model not available for this connection: {msg}")
    if r.status_code == 429:
        import time
        return GeminiUnavailable(f"Gemini rate limit / quota exceeded: {msg}",
                                 time.time() + config.GEMINI_MAX_WAIT_S)
    if "billing" in msg.lower():
        return GeminiAuthError(f"Gemini: {msg}")
    return GeminiError(f"Gemini error {r.status_code}: {msg}")
