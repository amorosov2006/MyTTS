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


class VertexAuth:
    """Google Cloud login (Application Default Credentials, e.g. `gcloud auth application-default
    login`) -> Vertex AI generateContent, billed to the ADC quota project."""
    method = "google_cloud"
    base_url = config.VERTEX_API_BASE

    def __init__(self, credentials=None, project: str | None = None):
        if credentials is None:
            credentials, project = _adc()
        self._creds, self.project = credentials, project
        if not project:
            raise GeminiAuthError("Google Cloud login has no project: run "
                                  "`gcloud auth application-default set-quota-project <PROJECT_ID>`.")

    async def headers(self) -> dict:
        if not self._creds.valid:
            import google.auth.transport.requests
            try:
                await asyncio.to_thread(self._creds.refresh, google.auth.transport.requests.Request())
            except Exception as e:
                raise GeminiAuthError(f"Google Cloud login expired or was revoked ({e}); run "
                                      "`gcloud auth application-default login` again.") from e
        return {"Authorization": f"Bearer {self._creds.token}", "x-goog-user-project": self.project}

    def model_path(self, model: str) -> str:
        return (f"/projects/{self.project}/locations/{config.VERTEX_LOCATION}"
                f"/publishers/google/models/{model}:generateContent")

    def check_path(self) -> str:
        return f"/publishers/google/models/{config.VERTEX_DEFAULT_MODEL}"


def _adc():
    import google.auth
    try:
        creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    except Exception as e:
        raise GeminiAuthError("No Google Cloud login found on this Mac.") from e
    return creds, project or getattr(creds, "quota_project_id", None)


def cloud_login_status() -> dict:
    """Local check only (no network): is there a usable Google Cloud login?"""
    try:
        _, project = _adc()
        return {"available": bool(project), "project": project}
    except GeminiAuthError:
        return {"available": False, "project": None}


def current_auth():
    """API key (if set) wins; otherwise the Google Cloud login; otherwise not connected."""
    from mytts import keystore
    key = keystore.gemini_key()
    if key:
        return ApiKeyAuth(key)
    if cloud_login_status()["available"]:
        return VertexAuth()
    raise GeminiAuthError("Google Gemini is not connected (⚙ → Cloud engines).")


def models_for(method: str | None) -> dict:
    return config.VERTEX_MODELS if method == "google_cloud" else config.GEMINI_MODELS


def default_model(method: str | None) -> str:
    return config.VERTEX_DEFAULT_MODEL if method == "google_cloud" else config.GEMINI_MODEL


def connection_status() -> dict:
    """What the UI shows: method in use (api_key wins), key tail, cloud project. No network."""
    from mytts import keystore
    key = keystore.gemini_key_status()
    cloud = cloud_login_status()
    method = "api_key" if key["configured"] else ("google_cloud" if cloud["available"] else None)
    return {**key, "configured": method is not None, "method": method,
            "cloud_project": cloud["project"], "cloud_available": cloud["available"]}


# Request dialects, tried in order until one is accepted (then remembered per model):
#   AI Studio 3.x: voiceConfig.voice + speech_metadata.style
#   Vertex 3.x:    voiceConfig.prebuiltVoiceConfig + speech_metadata.style
#   2.5 models:    voiceConfig.prebuiltVoiceConfig + style as a prompt prefix
DIALECTS = [("voice", "metadata"), ("prebuilt", "metadata"), ("prebuilt", "prefix")]


class GeminiClient:
    def __init__(self, auth, *, transport: httpx.AsyncBaseTransport | None = None):
        if isinstance(auth, str) or auth is None:
            auth = ApiKeyAuth(auth or "")
        self.auth = auth
        self._client = httpx.AsyncClient(base_url=auth.base_url, timeout=config.GEMINI_TIMEOUT_S,
                                         transport=transport, headers={"Content-Type": "application/json"})
        self._dialect: dict[str, int] = {}  # model -> index into DIALECTS known to work

    async def aclose(self) -> None:
        await self._client.aclose()

    async def check_key(self) -> None:
        """Cheap authenticated call. Raises GeminiAuthError if the credentials are rejected."""
        r = await self._client.get(self.auth.check_path(), headers=await self.auth.headers())
        if r.status_code != 200:
            raise _error_from(r)

    async def synthesize(self, text: str, *, voice: str, style: str, model: str) -> np.ndarray:
        """float32 mono audio at config.SAMPLE_RATE (24 kHz)."""
        known = model in self._dialect
        d = self._dialect.get(model, 2 if model.startswith("gemini-2.5") else 0)
        for attempt in range(config.GEMINI_MAX_RETRIES + len(DIALECTS)):
            r = await self._client.post(self.auth.model_path(model), headers=await self.auth.headers(),
                                        json=_body(text, voice, style, *DIALECTS[d]))
            if r.status_code == 200:
                self._dialect[model] = d
                return _decode(r.json())
            if r.status_code == 400 and not known and d + 1 < len(DIALECTS) and not _is_auth(r):
                d += 1  # e.g. Vertex rejects voiceConfig.voice with a generic "invalid argument"
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < config.GEMINI_MAX_RETRIES:
                delay = _retry_delay(r, attempt)
                log.warning("Gemini %s, retrying in %.1fs", r.status_code, delay)
                await asyncio.sleep(delay)
                continue
            raise _error_from(r)
        raise GeminiError("Gemini TTS failed after retries")


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


def _is_auth(r: httpx.Response) -> bool:
    msg = _message(r)
    return r.status_code in (401, 403) or "api key" in msg.lower() or "API_KEY" in msg


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
    if _is_auth(r):
        return GeminiAuthError(f"Gemini rejected the API key ({r.status_code}): {msg}")
    if r.status_code == 404:
        return GeminiError(f"Gemini model not available for this connection: {msg}")
    if r.status_code == 429:
        return GeminiError(f"Gemini rate limit / quota exceeded: {msg}")
    if "billing" in msg.lower() or "free tier" in msg.lower():
        return GeminiAuthError(f"Gemini: {msg}")
    return GeminiError(f"Gemini error {r.status_code}: {msg}")
