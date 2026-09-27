"""FastAPI app implementing docs/API.md. See create_app() for the DI points."""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import subprocess
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mytts import config
from mytts.contracts import (
    Event, IngestError, JobSettings, Lang, SampleRequest, SynthesisItem, SynthesisParams, TTSWorker,
)
from mytts.pipeline.events import EventBus
from mytts.pipeline.scheduler import ConflictError, NotFoundError, Scheduler
from mytts.pipeline.services import Services
from mytts.pipeline.store import Store
from mytts.voices import VoiceError, VoiceRegistry

log = logging.getLogger("mytts.api")

APP_VERSION = "0.1.0"
UPLOAD_MAX_BYTES = 200 * 1024 * 1024
CLONE_MAX_BYTES = 50 * 1024 * 1024
CLONE_ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aiff", ".caf"}
SSE_PING_S = 15
DEFAULT_ALLOWED_HOSTS = ["127.0.0.1", "localhost"]


def _origin_allowed(origin: str) -> bool:
    """Any port on 127.0.0.1/localhost is fine (covers the Vite dev server)."""
    try:
        hostname = urlparse(origin).hostname
    except ValueError:
        return False
    return hostname in ("127.0.0.1", "localhost")


class PickFolderRequest(BaseModel):
    start: Optional[str] = None


class ChapterPatch(BaseModel):
    index: int
    title: Optional[str] = None
    include: Optional[bool] = None


class VoiceDesignRequest(BaseModel):
    name: str
    lang: Lang
    description: str
    gender: Optional[str] = None


class GeminiKeyBody(BaseModel):
    api_key: str


class GeminiPreviewBody(BaseModel):
    voice: str
    lang: Lang = Lang.ru
    style: str = ""
    model: str = ""


def create_app(services: Optional[Services] = None, worker: Optional[TTSWorker] = None,
               data_dir: Optional[Path] = None,
               allowed_hosts: Optional[list[str]] = None,
               cloud_worker: Optional[TTSWorker] = None) -> FastAPI:
    if data_dir is not None:
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        # Confine every file this app instance touches (DB, voices, output) under data_dir.
        config.DATA_DIR = data_dir
        config.USER_VOICES_DIR = data_dir / "user_voices"
        config.DEFAULT_OUTPUT_DIR = data_dir / "output"

    services = services or Services()
    if worker is None:
        from mytts.pipeline.worker import ProcessWorker
        worker = ProcessWorker(engine="qwen")

    store = Store()
    bus = EventBus()
    voices = VoiceRegistry()
    if cloud_worker is None:
        from mytts.pipeline.cloud_worker import GeminiWorker
        cloud_worker = GeminiWorker()  # makes no network call until a job/preview uses it
    scheduler = Scheduler(store, services, bus, worker, voices=voices, cloud_worker=cloud_worker)
    bg_tasks: set[asyncio.Task] = set()

    def _track(task: "asyncio.Task") -> None:
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            await worker.start()
        except Exception as e:  # stays visible as worker state "failed"; retried on first use
            log.warning("worker did not start: %s", e)
        await scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()
            await worker.stop()
            if bg_tasks:
                await asyncio.gather(*list(bg_tasks), return_exceptions=True)

    app = FastAPI(title="MyTTS", version=APP_VERSION, lifespan=lifespan)
    app.state.store = store
    app.state.bus = bus
    app.state.scheduler = scheduler
    app.state.worker = worker
    app.state.voices = voices

    # DNS-rebinding / CSRF hardening: this server only ever expects the local UI to
    # talk to it. TrustedHostMiddleware checks Host; the Origin check below covers
    # requests a rebound page or a foreign site could still forge.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or DEFAULT_ALLOWED_HOSTS)

    @app.middleware("http")
    async def _check_origin(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and not _origin_allowed(origin):
                return JSONResponse(status_code=403, content={"detail": "origin not allowed"})
        return await call_next(request)

    @app.exception_handler(NotFoundError)
    async def _h_not_found(_req, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def _h_conflict(_req, exc: ConflictError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(IngestError)
    async def _h_ingest(_req, exc: IngestError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(VoiceError)
    async def _h_voice(_req, exc: VoiceError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    # ------------------------------------------------------------------ system

    @app.get("/api/health")
    async def health():
        return {"ok": True, "version": APP_VERSION}

    @app.get("/api/system")
    async def system():
        ws = worker.status()
        total = services.total_bytes()
        avail = services.available_bytes()
        fp = services.footprint(os.getpid())
        return {
            "worker": ws.model_dump(mode="json"),
            "memory": {
                "total_gb": total / 1e9, "available_gb": avail / 1e9,
                "app_footprint_gb": fp / 1e9, "cap_gb": 30,
            },
            "defaults": JobSettings().model_dump(mode="json"),
            "output_dir_default": str(config.DEFAULT_OUTPUT_DIR),
        }

    @app.post("/api/pick-folder")
    async def pick_folder(body: PickFolderRequest = PickFolderRequest()):
        def _run(start: Optional[str]) -> Optional[str]:
            # `start` is untrusted; it is passed as its own argv item (never interpolated
            # into the script text) so it can't break out into arbitrary AppleScript.
            use_start = bool(start) and Path(start).is_dir()
            cmd = ["osascript", "-e", "on run argv"]
            if use_start:
                cmd += [
                    "-e", 'set p to POSIX path of (choose folder with prompt'
                          ' "Choose output folder" default location (POSIX file (item 1 of argv)))',
                ]
            else:
                cmd += ["-e", 'set p to POSIX path of (choose folder with prompt "Choose output folder")']
            cmd += ["-e", "return p", "-e", "end run", "--"]
            if use_start:
                cmd.append(start)
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            except Exception:
                return None
            if out.returncode != 0:
                return None
            return out.stdout.strip() or None

        path = await asyncio.to_thread(_run, body.start)
        return {"path": path}

    # ---------------------------------------------------------------- engines / Gemini

    _PREVIEW_TEXT = {
        "ru": "Здравствуйте. Так будет звучать ваша книга, прочитанная этим голосом.",
        "en": "Hello. This is how your book will sound, read in this voice.",
    }

    @app.get("/api/engines")
    async def engines():
        from mytts.tts import gemini as gemini_mod
        from mytts.tts.gemini import VOICES
        return [
            {"id": "local", "name": "Qwen3-TTS on this Mac", "offline": True, "available": True},
            {"id": "gemini", "name": "Google Gemini TTS (cloud)", "offline": False,
             "available": (conn := gemini_mod.connection_status())["configured"],
             "key": conn,
             "default_model": gemini_mod.default_model(conn["method"]),
             "models": [{"id": m, "label": lbl, "usd_per_m_audio_tokens": usd, "free_tier": free}
                        for m, (lbl, usd, free) in gemini_mod.models_for(conn["method"]).items()],
             "usd_per_m_input_tokens": config.GEMINI_INPUT_USD_PER_M,
             "audio_tokens_per_second": config.GEMINI_AUDIO_TOKENS_PER_S,
             "default_voice": config.GEMINI_DEFAULT_VOICE,
             "default_style": config.GEMINI_DEFAULT_STYLE,
             "voices": [{"id": n, "name": n, "style": st, "gender": g} for n, (st, g) in VOICES.items()]},
        ]

    @app.get("/api/keys/gemini")
    async def gemini_key_status():
        from mytts.tts.gemini import connection_status
        return connection_status()

    @app.post("/api/keys/gemini/test")
    async def gemini_test_connection():
        """Verify the active connection (API key or Google Cloud login) with one cheap call."""
        from mytts.tts.gemini import GeminiError, connection_status
        try:
            await cloud_worker.check_connection()
        except GeminiError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"Could not reach Google: {e}")
        return {**connection_status(), "ok": True}

    @app.put("/api/keys/gemini")
    async def set_gemini_key(body: GeminiKeyBody):
        from mytts import keystore
        from mytts.tts.gemini import GeminiError
        key = body.api_key.strip()
        if not key:
            raise HTTPException(422, "Empty key")
        try:
            await cloud_worker.check_key(key)
        except GeminiError as e:
            raise HTTPException(400, str(e))
        except Exception as e:  # network down etc.
            raise HTTPException(502, f"Could not reach Google to verify the key: {e}")
        keystore.set_gemini_key(key)
        from mytts.tts.gemini import connection_status
        return connection_status()

    @app.delete("/api/keys/gemini", status_code=204)
    async def delete_gemini_key():
        from mytts import keystore
        keystore.delete_gemini_key()
        return Response(status_code=204)

    @app.post("/api/engines/gemini/preview")
    async def gemini_preview(body: GeminiPreviewBody):
        """A short sentence in a Gemini voice (one API request), cached on disk."""
        import hashlib
        from mytts.tts.gemini import VOICES, GeminiAuthError, GeminiError
        if body.voice not in VOICES:
            raise HTTPException(404, "unknown Gemini voice")
        model = cloud_worker.resolve_model(body.model)
        style = body.style or config.GEMINI_DEFAULT_STYLE[body.lang.value]
        key = hashlib.sha256(f"{model}|{body.voice}|{body.lang.value}|{style}".encode()).hexdigest()[:24]
        path = config.DATA_DIR / "gemini_previews" / f"{key}.wav"
        if not path.exists():
            from mytts.pipeline.cloud_worker import gemini_voice
            path.parent.mkdir(parents=True, exist_ok=True)
            item = SynthesisItem(segment_id="preview", text=_PREVIEW_TEXT[body.lang.value],
                                 lang=body.lang, out_wav=str(path))
            params = SynthesisParams(instruction=style)
            try:
                res = await cloud_worker.synthesize([item], gemini_voice(body.voice, body.lang, model),
                                                    params, qa=False)
            except (RuntimeError, GeminiError) as e:
                raise HTTPException(400 if isinstance(e, GeminiAuthError) else 502, str(e))
            if not res[0].ok:
                raise HTTPException(502, res[0].error or "Gemini preview failed")
        return FileResponse(path, media_type="audio/wav")

    @app.get("/api/formats")
    async def formats():
        try:
            from mytts.ingest import SUPPORTED_EXTENSIONS
        except ImportError:
            SUPPORTED_EXTENSIONS = []
        return {"extensions": list(SUPPORTED_EXTENSIONS)}

    # ------------------------------------------------------------------ voices

    @app.get("/api/voices")
    async def list_voices():
        out = []
        for v in voices.list():
            d = v.model_dump(mode="json")
            d.pop("ref_audio", None)
            d["preview_url"] = f"/api/voices/{v.id}/preview"
            out.append(d)
        return out

    @app.get("/api/voices/{voice_id}/preview")
    async def voice_preview(voice_id: str):
        v = voices.get(voice_id)
        if v is None:
            raise HTTPException(404, "voice not found")
        return FileResponse(v.ref_audio, media_type="audio/wav")

    @app.post("/api/voices/design", status_code=202)
    async def design_voice(req: VoiceDesignRequest):
        if scheduler.has_running_job():
            raise HTTPException(409, "a job is running")
        voice_id = f"design_{uuid.uuid4().hex[:8]}"

        async def _run():
            try:
                async with scheduler.worker_lock:
                    v = await voices.add_designed(worker, req.description, req.lang, req.name,
                                                   req.gender, voice_id=voice_id)
                bus.publish(Event(type="voice", data={"voice": v.model_dump(mode="json"),
                                                      "status": "ready"}))
            except Exception as e:  # noqa: BLE001 - report failure to the UI, never crash the app
                bus.publish(Event(type="voice", data={"voice_id": voice_id, "status": "failed",
                                                      "error": str(e)}))

        _track(asyncio.ensure_future(_run()))
        return {"voice_id": voice_id}

    @app.post("/api/voices/clone", status_code=201)
    async def clone_voice(audio: UploadFile = File(...), transcript: str = Form(...),
                           name: str = Form(...), lang: Lang = Form(...),
                           gender: Optional[str] = Form(None)):
        suffix = Path(audio.filename or "clip").suffix.lower() or ".wav"
        if suffix not in CLONE_ALLOWED_EXTENSIONS:
            raise HTTPException(415, f"unsupported audio format: {suffix}")
        fd, tmp_name = tempfile.mkstemp(suffix=suffix)
        tmp_path = Path(tmp_name)
        try:
            size = 0
            with os.fdopen(fd, "wb") as f:
                while True:
                    chunk = await audio.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > CLONE_MAX_BYTES:
                        raise HTTPException(413, "audio file too large")
                    f.write(chunk)
            v = await asyncio.to_thread(voices.add_cloned, tmp_path, transcript, name, lang, gender)
        finally:
            tmp_path.unlink(missing_ok=True)
        return v.model_dump(mode="json")

    @app.delete("/api/voices/{voice_id}", status_code=204)
    async def delete_voice(voice_id: str):
        try:
            voices.delete(voice_id)
        except KeyError:
            raise HTTPException(404, "voice not found")
        except PermissionError:
            raise HTTPException(403, "built-in voices cannot be deleted")
        return Response(status_code=204)

    # ------------------------------------------------------------------ jobs

    @app.post("/api/jobs", status_code=201)
    async def create_job(file: UploadFile = File(...)):
        suffix = Path(file.filename or "upload").suffix
        fd, tmp_name = tempfile.mkstemp(suffix=suffix)
        tmp_path = Path(tmp_name)
        try:
            size = 0
            with os.fdopen(fd, "wb") as f:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > UPLOAD_MAX_BYTES:
                        raise HTTPException(413, "file too large")
                    f.write(chunk)
            info = await scheduler.create_job(tmp_path, file.filename or "upload")
        finally:
            tmp_path.unlink(missing_ok=True)
        return info.model_dump(mode="json")

    @app.get("/api/jobs")
    async def list_jobs():
        return [j.model_dump(mode="json") for j in scheduler.list_jobs()]

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        return scheduler.get_job(job_id).model_dump(mode="json")

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def delete_job(job_id: str):
        await scheduler.delete_job(job_id)
        return Response(status_code=204)

    @app.get("/api/jobs/{job_id}/cover")
    async def job_cover(job_id: str):
        if not store.job_exists(job_id):
            raise HTTPException(404)
        book = store.read_book_json(job_id)
        if not book.get("cover"):
            raise HTTPException(404)
        return Response(content=base64.b64decode(book["cover"]),
                         media_type=book.get("cover_mime") or "image/jpeg")

    @app.get("/api/jobs/{job_id}/chapters/{n}/text")
    async def chapter_text(job_id: str, n: int):
        if not store.job_exists(job_id):
            raise HTTPException(404)
        book = store.read_book_json(job_id)
        if n < 0 or n >= len(book["chapters"]):
            raise HTTPException(404)
        title = next((c.title for c in store.get_chapters(job_id) if c.index == n),
                     book["chapters"][n].get("title", ""))
        return {"title": title, "paragraphs": book["chapters"][n]["paragraphs"]}

    @app.patch("/api/jobs/{job_id}/chapters")
    async def patch_chapters(job_id: str, updates: list[ChapterPatch]):
        info = scheduler.update_chapters(job_id, [u.model_dump() for u in updates])
        return info.model_dump(mode="json")

    @app.put("/api/jobs/{job_id}/settings")
    async def put_settings(job_id: str, settings: JobSettings):
        info = scheduler.update_settings(job_id, settings)
        return info.model_dump(mode="json")

    @app.post("/api/jobs/{job_id}/samples", status_code=202)
    async def post_sample(job_id: str, req: SampleRequest):
        info = await scheduler.create_sample(job_id, req)
        return info.model_dump(mode="json")

    @app.get("/api/jobs/{job_id}/samples")
    async def get_samples(job_id: str):
        if not store.job_exists(job_id):
            raise HTTPException(404)
        return [s.model_dump(mode="json") for s in scheduler.list_samples(job_id)]

    @app.post("/api/jobs/{job_id}/samples/{sample_id}/approve")
    async def approve_sample(job_id: str, sample_id: str):
        info = scheduler.approve_sample(job_id, sample_id)
        return info.model_dump(mode="json")

    @app.get("/api/jobs/{job_id}/samples/{sample_id}/audio")
    async def sample_audio(job_id: str, sample_id: str):
        row = store.get_sample(sample_id)
        if row is None or row["job_id"] != job_id or row["status"] != "done":
            raise HTTPException(404)
        path = store.sample_output_path(job_id, sample_id)
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="audio/mpeg")

    # Not in docs/API.md: exposed so the player can progressively play a sample's segments
    # as they complete, matching SampleInfo.segments (see final report "deviations").
    @app.get("/api/jobs/{job_id}/samples/{sample_id}/segments/{segment_id}/audio")
    async def sample_segment_audio(job_id: str, sample_id: str, segment_id: str):
        row = store.get_sample(sample_id)
        if row is None or row["job_id"] != job_id:
            raise HTTPException(404)
        for r in store.sample_segments_ordered(sample_id):
            if r["id"] == segment_id and r["status"] == "done" and r["processed_wav"]:
                return FileResponse(r["processed_wav"], media_type="audio/wav")
        raise HTTPException(404)

    @app.post("/api/jobs/{job_id}/start")
    async def start_job(job_id: str):
        info = await scheduler.start_job(job_id)
        return info.model_dump(mode="json")

    @app.post("/api/jobs/{job_id}/pause")
    async def pause_job(job_id: str):
        return scheduler.pause_job(job_id).model_dump(mode="json")

    @app.post("/api/jobs/{job_id}/resume")
    async def resume_job(job_id: str):
        return scheduler.resume_job(job_id).model_dump(mode="json")

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        return scheduler.cancel_job(job_id).model_dump(mode="json")

    @app.post("/api/jobs/{job_id}/duplicate", status_code=201)
    async def duplicate_job(job_id: str):
        return (await scheduler.duplicate_job(job_id)).model_dump(mode="json")

    @app.post("/api/jobs/{job_id}/reveal", status_code=204)
    async def reveal_job(job_id: str):
        info = scheduler.get_job(job_id)
        if not info.output_path or not Path(info.output_path).is_dir():
            raise HTTPException(404, "no output folder yet")
        await asyncio.to_thread(subprocess.run, ["open", info.output_path], timeout=10)
        return Response(status_code=204)

    @app.get("/api/jobs/{job_id}/segments/{segment_id}/audio")
    async def segment_audio(job_id: str, segment_id: str):
        row = store.get_segment(job_id, segment_id)
        if row is None or row["status"] != "done" or not row["processed_wav"]:
            raise HTTPException(404)
        return FileResponse(row["processed_wav"], media_type="audio/wav")

    @app.get("/api/jobs/{job_id}/chapters/{n}/audio")
    async def chapter_audio(job_id: str, n: int):
        path = store.get_chapter_file(job_id, n)
        if not path or not Path(path).is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="audio/mpeg")

    @app.get("/api/jobs/{job_id}/chapters/{n}/segments")
    async def chapter_segments(job_id: str, n: int):
        if not store.job_exists(job_id):
            raise HTTPException(404)
        rows = store.chapter_segments(job_id, n)
        return [
            {"segment_id": r["id"], "url": f"/api/jobs/{job_id}/segments/{r['id']}/audio",
             "duration_s": r["duration_s"], "pause_after_ms": r["pause_after_ms"]}
            for r in rows if r["status"] == "done"
        ]

    # ------------------------------------------------------------------ SSE

    async def _event_stream(request: Request, job_id: Optional[str]):
        sid, q = bus.subscribe()
        try:
            if job_id is not None:
                info = store.get_job_info(job_id)
                if info is not None:
                    yield _sse(Event(type="job", job_id=job_id, data=info.model_dump(mode="json")))
            else:
                for info in store.list_jobs():
                    yield _sse(Event(type="job", job_id=info.id, data=info.model_dump(mode="json")))
                yield _sse(Event(type="worker", data=worker.status().model_dump(mode="json")))
            while True:
                if await request.is_disconnected():
                    break
                event = await q.get()
                if job_id is not None and event.job_id != job_id:
                    continue
                yield _sse(event)
        finally:
            bus.unsubscribe(sid)

    def _sse(event: Event) -> dict:
        return {"event": event.type, "data": event.model_dump_json()}

    @app.get("/api/events")
    async def all_events(request: Request):
        return EventSourceResponse(_event_stream(request, None), ping=SSE_PING_S)

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(request: Request, job_id: str):
        if not store.job_exists(job_id):
            raise HTTPException(404)
        return EventSourceResponse(_event_stream(request, job_id), ping=SSE_PING_S)

    # ------------------------------------------------------------------ static / SPA

    index_html = config.STATIC_DIR / "index.html"
    if index_html.is_file():
        static_root = config.STATIC_DIR.resolve()

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            candidate = (config.STATIC_DIR / full_path).resolve()
            if full_path and candidate.is_relative_to(static_root) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_html)
    else:
        @app.get("/")
        async def placeholder():
            return HTMLResponse(
                "<html><body><h1>MyTTS</h1><p>Frontend not built yet — the API is at /api.</p>"
                "</body></html>"
            )

    return app
