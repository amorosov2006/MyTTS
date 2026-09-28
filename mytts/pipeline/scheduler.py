"""Job scheduler: one FIFO of jobs, one GPU worker, a CPU pool for post-processing.

Dispatch loop (`_dispatch_loop`) picks, in order, on every tick:
  1. pending segments of the oldest sample still being rendered (samples preempt jobs), else
  2. the next batch of pending segments for the active job (head of the run queue), else
  3. it sleeps until woken (new work, or a periodic tick to catch background completions).

Synthesis (`worker.synthesize`) and CPU post-processing (`services.process_segment` /
`assemble` / `build_m4b`, run in a process pool) are pipelined: post-processing of one
batch runs in the background while the next batch is already being synthesized.
"""
from __future__ import annotations

import asyncio
import base64
import functools
import logging
import os
import time
from concurrent.futures import Executor, ProcessPoolExecutor
from typing import Optional

from mytts import config
from mytts.contracts import (
    Book, Chapter, EngineName, Event, JobInfo, JobSettings, JobStatus, Lang, OutputFormat, Progress,
    SampleInfo, SampleRequest, Segment, SynthesisItem, SynthesisResult, TTSWorker,
    WorkerCrashed,
)
from mytts.pipeline.cloud_worker import gemini_voice
from mytts.pipeline.events import EventBus
from mytts.pipeline.services import Services
from mytts.pipeline.store import Store, new_id, sanitize_filename
from mytts.voices import VoiceRegistry

log = logging.getLogger("mytts.scheduler")

MAX_BATCH_RETRIES = 3
FALLBACK_CHARS_PER_S = 14.0
FALLBACK_REALTIME_X = 8.0
_IDLE_TICK_S = 1.0


class VoiceMissing(Exception):
    pass


class NotFoundError(Exception):
    """Maps to HTTP 404."""


class ConflictError(Exception):
    """Maps to HTTP 409."""


def _same_lang_prefix(rows: list) -> list:
    if not rows:
        return rows
    lang = rows[0]["lang"]
    out = []
    for r in rows:
        if r["lang"] != lang:
            break
        out.append(r)
    return out


def _exit_with_parent(parent_pid: int) -> None:
    """Pool-worker initializer: if the app is killed (even SIGKILL), don't linger as an orphan."""
    import threading

    def watch() -> None:
        while os.getppid() == parent_pid:
            time.sleep(1.0)
        os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


class Scheduler:
    def __init__(self, store: Store, services: Services, bus: EventBus, worker: TTSWorker,
                 voices: Optional[VoiceRegistry] = None, executor: Optional[Executor] = None,
                 max_cpu_workers: int = 3, cloud_worker: Optional[TTSWorker] = None):
        self.cloud_worker = cloud_worker  # Gemini (optional); `worker` is the local GPU engine
        self.store = store
        self.services = services
        self.bus = bus
        self.worker = worker
        self.voices = voices or VoiceRegistry()
        self.worker_lock = asyncio.Lock()
        self._owns_executor = executor is None
        self._executor = executor or ProcessPoolExecutor(
            max_workers=max_cpu_workers, initializer=_exit_with_parent, initargs=(os.getpid(),))
        self._run_queue: list[str] = []
        self._job_stats: dict[str, dict] = {}
        self._retries: dict[str, int] = {}
        self._finalizing: set[str] = set()
        self._inflight: set[tuple[str, str]] = set()    # (job_id, segment_id) synth/post-processing
        self._assembling: set[tuple[str, int]] = set()  # (job_id, chapter) being assembled now
        self._preparing: set[str] = set()               # jobs inside start_job (double-click guard)
        self._wakeup = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._bg_tasks: set[asyncio.Task] = set()
        self._stopped = True

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        for info in self.store.list_jobs():
            if info.status in (JobStatus.running, JobStatus.queued):
                self.store.reset_running_segments(info.id)
                self.store.update_job(info.id, status=JobStatus.paused,
                                       error="Interrupted — press Resume")
            self._unstick_chapters(info.id)
        self.store.reset_running_sample_segments()
        self._stopped = False
        self._task = asyncio.create_task(self._dispatch_loop())

    async def stop(self, timeout_s: float = 5.0) -> None:
        self._stopped = True
        self._wakeup.set()
        if self._task is not None:
            try:  # the loop may be inside a long synthesize call: don't hang shutdown on it
                await asyncio.wait_for(asyncio.shield(self._task), timeout_s)
            except asyncio.TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
        if self._bg_tasks:
            await asyncio.wait(list(self._bg_tasks), timeout=timeout_s)
        if self._owns_executor:
            self._executor.shutdown(wait=True)

    def _unstick_chapters(self, job_id: str) -> None:
        """A chapter left 'assembling' (app killed mid-assembly) would block its job forever:
        put it back to 'running' so it is assembled again once its segments are finished."""
        for c in self.store.get_chapters(job_id):
            if c.status == "assembling" and (job_id, c.index) not in self._assembling:
                self.store.update_chapter_state(job_id, c.index, status="running")

    def _reassemble_ready_chapters(self, job_id: str) -> None:
        settings = self.store.get_job_settings(job_id)
        for c in self.store.get_chapters(job_id):
            if c.include and c.status in ("running", "pending") and c.segments_total:
                self._track_bg(asyncio.ensure_future(self._maybe_finish_chapter(job_id, c.index, settings)))

    def _track_bg(self, task: "asyncio.Task") -> None:
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def has_running_job(self) -> bool:
        return any(
            self.store.get_job_status(j) in (JobStatus.running, JobStatus.queued)
            for j in self._run_queue
        )

    # ------------------------------------------------------------------ job CRUD

    def _require_job(self, job_id: str) -> JobInfo:
        info = self.store.get_job_info(job_id)
        if info is None:
            raise NotFoundError(f"job not found: {job_id}")
        return info

    async def create_job(self, uploaded_file_path, original_filename: str) -> JobInfo:
        import shutil
        import tempfile
        from pathlib import Path

        # parse_book(path) has nothing but the path to go on for formats without in-file
        # metadata (e.g. .txt), so re-home the upload under its original name/extension first.
        suffix = Path(original_filename).suffix
        stem = Path(original_filename).stem or "upload"
        tmp_dir = Path(tempfile.mkdtemp(prefix="mytts_upload_"))
        named_path = tmp_dir / f"{stem}{suffix}"
        await asyncio.to_thread(shutil.copy, uploaded_file_path, named_path)
        try:
            book = await asyncio.to_thread(self.services.parse_book, named_path)
            settings = JobSettings()
            if book.lang == Lang.en:
                settings.voice_id = "en_male"
            job_id = await asyncio.to_thread(self.store.create_job, book, settings, named_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return self.store.get_job_info(job_id)

    async def duplicate_job(self, job_id: str) -> JobInfo:
        """"Convert again": a fresh job (status parsed) with the same book, chapter edits and
        settings, so a finished book can be re-rendered with another voice/style. Its output
        goes to its own folder ("... (2)")."""
        info = self._require_job(job_id)
        bj = self.store.read_book_json(job_id)
        if bj.get("cover"):
            bj["cover"] = base64.b64decode(bj["cover"])
        book = Book.model_validate(bj)
        upload = next(self.store.job_workdir(job_id).glob("upload.*"))
        new_id_ = await asyncio.to_thread(self.store.create_job, book, info.settings, upload)
        self.store.update_chapters_meta(
            new_id_, [{"index": c.index, "title": c.title, "include": c.include} for c in info.chapters])
        return self.store.get_job_info(new_id_)

    def get_job(self, job_id: str) -> JobInfo:
        return self._require_job(job_id)

    def list_jobs(self) -> list[JobInfo]:
        return self.store.list_jobs()

    async def delete_job(self, job_id: str) -> None:
        self._require_job(job_id)
        self._run_queue = [j for j in self._run_queue if j != job_id]
        await asyncio.to_thread(self.store.delete_job, job_id)

    def update_chapters(self, job_id: str, updates: list[dict]) -> JobInfo:
        info = self._require_job(job_id)
        if info.status not in (JobStatus.parsed, JobStatus.paused):
            raise ConflictError(f"cannot edit chapters in status {info.status.value}")
        self.store.update_chapters_meta(job_id, updates)
        return self.store.get_job_info(job_id)

    def update_settings(self, job_id: str, settings: JobSettings) -> JobInfo:
        info = self._require_job(job_id)
        if info.status not in (JobStatus.parsed, JobStatus.paused):
            raise ConflictError(f"cannot change settings in status {info.status.value}")
        old_hash = self.store.get_settings_hash(job_id)
        self.store.set_settings(job_id, settings)
        if info.sample_approved and self.store.get_settings_hash(job_id) != old_hash:
            self.store.update_job(job_id, sample_approved=False)
        return self.store.get_job_info(job_id)

    # ------------------------------------------------------------------ run control

    async def start_job(self, job_id: str) -> JobInfo:
        info = self._require_job(job_id)
        if info.status != JobStatus.parsed or job_id in self._preparing:
            raise ConflictError(f"cannot start a job in status {info.status.value}")
        self._preparing.add(job_id)
        try:
            return await self._start_job(job_id, info)
        finally:
            self._preparing.discard(job_id)

    async def _prepare_chapter(self, job_id: str, cm, book_json: dict, settings: JobSettings) -> None:
        cdata = book_json["chapters"][cm.index]
        chapter = Chapter(index=cm.index, title=cm.title, paragraphs=cdata["paragraphs"],
                          include=True, kind=cdata.get("kind", "body"))
        lang = settings.lang or (Lang(book_json["lang"]) if book_json.get("lang") else None)
        if lang is None:
            sample_text = "\n".join(chapter.paragraphs[:3]) or chapter.title or "."
            lang = await asyncio.to_thread(self.services.detect_lang, sample_text)
        segments = await asyncio.to_thread(
            self.services.prepare_chapter, chapter, lang,
            read_title=settings.read_titles, skip_footnotes=settings.skip_footnotes,
            pause_sentence_ms=settings.pause_sentence_ms,
            pause_paragraph_ms=settings.pause_paragraph_ms,
            **self._segment_sizes(settings),
        )
        if segments:
            self.store.add_segments(job_id, cm.index, segments)
            self.store.update_chapter_state(job_id, cm.index, status="pending")
        else:
            self.store.update_chapter_state(job_id, cm.index, status="skipped", segments_total=0)

    async def _sync_chapter_selection(self, job_id: str) -> None:
        """Chapters (un)ticked while paused: prepare newly included ones, drop the queued work
        of excluded ones (finished audio is kept)."""
        info = self.store.get_job_info(job_id)
        book_json = self.store.read_book_json(job_id)
        prepared = self.store.chapter_indexes_with_segments(job_id)
        for cm in info.chapters:
            if cm.include and cm.index not in prepared and cm.status not in ("done", "skipped"):
                await self._prepare_chapter(job_id, cm, book_json, info.settings)
            elif not cm.include and cm.index in prepared:
                self.store.delete_pending_segments(job_id, cm.index)
                self.store.update_chapter_state(job_id, cm.index, status="skipped")

    async def _start_job(self, job_id: str, info: JobInfo) -> JobInfo:
        book_json = self.store.read_book_json(job_id)
        for cm in self.store.get_chapters(job_id):
            if cm.include:
                await self._prepare_chapter(job_id, cm, book_json, info.settings)
        self.store.update_job(job_id, status=JobStatus.queued)
        self.store.claim_output_dir(job_id, info.settings, book_json.get("author"), book_json["title"])
        self._job_stats[job_id] = {"started_at": time.monotonic(), "busy_s": 0.0}
        self._update_progress(job_id)
        if job_id not in self._run_queue:
            self._run_queue.append(job_id)
        self._wakeup.set()
        self._publish_job(job_id, force=True)
        return self.store.get_job_info(job_id)

    def pause_job(self, job_id: str) -> JobInfo:
        info = self._require_job(job_id)
        if info.status not in (JobStatus.queued, JobStatus.running):
            raise ConflictError(f"cannot pause a job in status {info.status.value}")
        self._run_queue = [j for j in self._run_queue if j != job_id]
        self.store.update_job(job_id, status=JobStatus.paused)
        self._publish_job(job_id, force=True)
        return self.store.get_job_info(job_id)

    async def resume_job(self, job_id: str) -> JobInfo:
        info = self._require_job(job_id)
        if info.status != JobStatus.paused or job_id in self._preparing:
            raise ConflictError(f"cannot resume a job in status {info.status.value}")
        self._preparing.add(job_id)
        try:
            await self._sync_chapter_selection(job_id)
        finally:
            self._preparing.discard(job_id)
        self.store.reset_running_segments(
            job_id, exclude=frozenset(s for j, s in self._inflight if j == job_id))
        self._unstick_chapters(job_id)
        self._reassemble_ready_chapters(job_id)
        self.store.update_job(job_id, status=JobStatus.queued, error=None)
        self._job_stats.setdefault(job_id, {"started_at": time.monotonic(), "busy_s": 0.0})
        if job_id not in self._run_queue:
            self._run_queue.append(job_id)
        self._wakeup.set()
        self._publish_job(job_id, force=True)
        return self.store.get_job_info(job_id)

    def cancel_job(self, job_id: str) -> JobInfo:
        info = self._require_job(job_id)
        if info.status not in (JobStatus.queued, JobStatus.running, JobStatus.paused):
            raise ConflictError(f"cannot cancel a job in status {info.status.value}")
        self._run_queue = [j for j in self._run_queue if j != job_id]
        self._job_stats.pop(job_id, None)
        self.store.update_job(job_id, status=JobStatus.cancelled)
        self._publish_job(job_id, force=True)
        return self.store.get_job_info(job_id)

    # ------------------------------------------------------------------ samples

    async def create_sample(self, job_id: str, req: SampleRequest) -> SampleInfo:
        info = self._require_job(job_id)
        if info.status not in (JobStatus.parsed, JobStatus.paused, JobStatus.running,
                               JobStatus.done, JobStatus.cancelled):
            raise ConflictError(f"cannot sample a job in status {info.status.value}")
        book_json = self.store.read_book_json(job_id)
        settings = info.settings
        lang = settings.lang or (Lang(book_json["lang"]) if book_json.get("lang") else Lang.ru)
        if req.text:
            paragraphs = [p for p in req.text.split("\n") if p.strip()] or [req.text]
            title = ""
        else:
            included = [c for c in self.store.get_chapters(job_id) if c.include]
            if not included:
                raise ConflictError("no chapters included")
            if req.chapter is not None:
                chosen = next((c for c in included if c.index == req.chapter), included[len(included) // 2])
            else:
                chosen = included[len(included) // 2]
            cdata = book_json["chapters"][chosen.index]
            all_paragraphs: list[str] = cdata["paragraphs"]
            offset = 0.5 if req.offset is None else max(0.0, min(1.0, req.offset))
            total_chars = sum(len(p) for p in all_paragraphs) or 1
            target_start = int(total_chars * offset)
            target_chars = req.seconds * FALLBACK_CHARS_PER_S
            paragraphs, seen, taken = [], 0, 0
            for p in all_paragraphs:
                if not paragraphs and seen + len(p) <= target_start:
                    seen += len(p)
                    continue
                paragraphs.append(p)
                taken += len(p)
                if taken >= target_chars:
                    break
            paragraphs = paragraphs or all_paragraphs[:1]
            title = chosen.title
        chapter = Chapter(index=0, title=title, paragraphs=paragraphs, include=True, kind="body")
        segments = await asyncio.to_thread(
            self.services.prepare_chapter, chapter, lang, read_title=False,
            skip_footnotes=settings.skip_footnotes, pause_sentence_ms=settings.pause_sentence_ms,
            pause_paragraph_ms=settings.pause_paragraph_ms, **self._segment_sizes(settings),
        )
        if not segments:
            raise ConflictError("Nothing to read in the sample text")
        sid = new_id("smp")
        segments = [s.model_copy(update={"id": f"{sid}-{i:04d}"}) for i, s in enumerate(segments)]
        text = "\n\n".join(paragraphs)
        self.store.create_sample(sid, job_id, settings, text, segments)
        self._wakeup.set()
        return self.store.sample_to_info(self.store.get_sample(sid))

    def get_sample(self, job_id: str, sample_id: str) -> SampleInfo:
        row = self.store.get_sample(sample_id)
        if row is None or row["job_id"] != job_id:
            raise NotFoundError("sample not found")
        return self.store.sample_to_info(row)

    def list_samples(self, job_id: str) -> list[SampleInfo]:
        return [self.store.sample_to_info(r) for r in self.store.list_samples(job_id)]

    def approve_sample(self, job_id: str, sample_id: str) -> JobInfo:
        row = self.store.get_sample(sample_id)
        if row is None or row["job_id"] != job_id:
            raise NotFoundError("sample not found")
        approved = row["settings_hash"] == self.store.get_settings_hash(job_id)
        self.store.update_job(job_id, sample_approved=approved)
        self._publish_job(job_id, force=True)
        return self.store.get_job_info(job_id)

    # ------------------------------------------------------------------ dispatch loop

    async def _dispatch_loop(self) -> None:
        while not self._stopped:
            try:
                await self._dispatch_once()
            except Exception:
                log.exception("dispatch loop error")
                await asyncio.sleep(_IDLE_TICK_S)

    async def _dispatch_once(self) -> None:
        sample_batch = self._pick_sample_batch()
        if sample_batch is not None:
            await self._run_sample_batch(*sample_batch)
            return
        job_batch = self._pick_job_batch()
        if job_batch is not None:
            await self._run_job_batch(*job_batch)
            return
        if self._run_queue and self.store.get_job_status(self._run_queue[0]) == JobStatus.running:
            self._track_bg(asyncio.ensure_future(self._maybe_finish_job(self._run_queue[0])))
        self._wakeup.clear()
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout=_IDLE_TICK_S)
        except asyncio.TimeoutError:
            pass

    def _pick_sample_batch(self):
        srow = self.store.oldest_pending_sample()
        if srow is None:
            return None
        sample_id = srow["id"]
        if srow["status"] == "queued":
            self.store.update_sample(sample_id, status="running")
        rows = self.store.next_pending_sample_segments_for(sample_id, config.BATCH_SIZE)
        rows = _same_lang_prefix(rows)
        return (sample_id, rows) if rows else None

    def _pick_job_batch(self):
        if not self._run_queue:
            return None
        job_id = self._run_queue[0]
        status = self.store.get_job_status(job_id)
        if status == JobStatus.queued:
            self.store.update_job(job_id, status=JobStatus.running)
            self._publish_job(job_id, force=True)
        elif status != JobStatus.running:
            self._run_queue.pop(0)
            return None
        rows = _same_lang_prefix(self.store.next_pending_segments(job_id, config.BATCH_SIZE))
        return (job_id, rows) if rows else None

    def _engine_for(self, settings: JobSettings, lang: Lang):
        """(worker, voice, params, needs_gpu_lock) for a batch in `lang`. voice is None if the
        local voice doesn't exist."""
        if settings.engine == EngineName.gemini:
            if self.cloud_worker is None:
                raise RuntimeError("The Gemini engine is not available in this app instance")
            name = settings.gemini_voice or config.GEMINI_DEFAULT_VOICE[lang.value]
            model = settings.gemini_model  # "" -> the active connection's default (cloud worker)
            style = settings.gemini_style or config.GEMINI_DEFAULT_STYLE[lang.value]
            params = settings.params.model_copy(update={"instruction": style})
            return self.cloud_worker, gemini_voice(name, lang, model), params, False
        return self.worker, self.voices.get(settings.voice_id), settings.params, True

    async def _synthesize(self, settings: JobSettings, items: list[SynthesisItem]):
        worker, voice, params, gpu = self._engine_for(settings, items[0].lang)
        if voice is None:
            raise VoiceMissing(f"voice not found: {settings.voice_id}")
        if gpu:
            async with self.worker_lock:
                return await worker.synthesize(items, voice, params, qa=settings.qa)
        return await worker.synthesize(items, voice, params, qa=settings.qa)

    @staticmethod
    def _segment_sizes(settings: JobSettings) -> dict:
        if settings.engine == EngineName.gemini:
            return {"target_chars": config.GEMINI_SEGMENT_TARGET_CHARS,
                    "max_chars": config.GEMINI_SEGMENT_MAX_CHARS, "join_paragraphs": True}
        return {}

    async def _run_job_batch(self, job_id: str, rows) -> None:
        settings = self.store.get_job_settings(job_id)
        if settings.engine == EngineName.local and self.voices.get(settings.voice_id) is None:
            self._pause_job_with_error(job_id, f"voice not found: {settings.voice_id}")
            return
        for r in rows:
            self.store.mark_segment_running(job_id, r["id"])
            self._inflight.add((job_id, r["id"]))
        items = [
            SynthesisItem(segment_id=r["id"], text=r["text"], lang=Lang(r["lang"]),
                          out_wav=str(self.store.raw_wav_path(job_id, r["id"])))
            for r in rows
        ]
        t0 = time.monotonic()
        try:
            results = await self._synthesize(settings, items)
        except WorkerCrashed:
            for r in rows:
                self.store.mark_segment_pending(job_id, r["id"])
                self._inflight.discard((job_id, r["id"]))
            n = self._retries[job_id] = self._retries.get(job_id, 0) + 1
            if n > MAX_BATCH_RETRIES:
                self._retries.pop(job_id, None)
                self._pause_job_with_error(job_id, "TTS worker crashed repeatedly; paused.")
            else:
                self._log(job_id, "warning", f"TTS worker crashed, retrying batch (attempt {n})")
            self._wakeup.set()
            return
        except Exception as e:
            log.exception("synthesis failed")
            for r in rows:
                self.store.mark_segment_pending(job_id, r["id"])
                self._inflight.discard((job_id, r["id"]))
            self._pause_job_with_error(job_id, f"TTS error: {e}")
            return
        self._retries.pop(job_id, None)
        elapsed = time.monotonic() - t0
        stats = self._job_stats.setdefault(job_id, {"started_at": time.monotonic(), "busy_s": 0.0})
        stats["busy_s"] = stats.get("busy_s", 0.0) + elapsed
        for r, res in zip(rows, results):
            self._track_bg(asyncio.ensure_future(self._post_process(job_id, r, res, settings)))
        self._wakeup.set()

    def _pause_job_with_error(self, job_id: str, message: str) -> None:
        self._run_queue = [j for j in self._run_queue if j != job_id]
        self.store.update_job(job_id, status=JobStatus.paused, error=message)
        self._publish_job(job_id, force=True)

    async def _post_process(self, job_id: str, seg_row, result: SynthesisResult,
                             settings: JobSettings) -> None:
        try:
            await self._post_process_inner(job_id, seg_row, result, settings)
        finally:
            self._inflight.discard((job_id, seg_row["id"]))

    async def _post_process_inner(self, job_id: str, seg_row, result: SynthesisResult,
                                  settings: JobSettings) -> None:
        if not self.store.job_exists(job_id):  # deleted while in flight
            return
        if not result.ok:
            self.store.mark_segment_failed(job_id, seg_row["id"], result.error or "synthesis failed")
            self._log(job_id, "warning", f"segment {seg_row['id']} failed: {result.error}")
        else:
            out_wav = self.store.processed_wav_path(job_id, seg_row["id"])
            loop = asyncio.get_running_loop()
            try:
                duration = await loop.run_in_executor(
                    self._executor, self.services.process_segment, result.out_wav, str(out_wav),
                    settings.speed,
                )
            except Exception as e:
                log.exception("post-processing failed")
                self.store.mark_segment_failed(job_id, seg_row["id"], f"post-processing: {e}")
                self._log(job_id, "warning", f"segment {seg_row['id']} post-processing failed: {e}")
                self._update_chapter_progress(job_id, seg_row["chapter"])
                self._update_progress(job_id)
                await self._maybe_finish_chapter(job_id, seg_row["chapter"], settings)
                return
            if not self.store.job_exists(job_id):
                return
            self.store.mark_segment_done(job_id, seg_row["id"], str(out_wav), duration, result.cer,
                                          result.transcript, result.attempts)
            self.bus.publish(Event(type="segment", job_id=job_id, data={
                "segment_id": seg_row["id"], "chapter": seg_row["chapter"], "index": seg_row["idx"],
                "url": f"/api/jobs/{job_id}/segments/{seg_row['id']}/audio",
                "duration_s": duration, "cer": result.cer,
            }))
        self._update_chapter_progress(job_id, seg_row["chapter"])
        self._update_progress(job_id)
        await self._maybe_finish_chapter(job_id, seg_row["chapter"], settings)

    def _update_chapter_progress(self, job_id: str, chapter: int) -> None:
        counts = self.store.chapter_segment_counts(job_id, chapter)
        cs = next((c for c in self.store.get_chapters(job_id) if c.index == chapter), None)
        if cs is None:
            return
        update = {"segments_done": counts.get("done", 0) + counts.get("failed", 0)}
        if cs.status == "pending":
            update["status"] = "running"
        self.store.update_chapter_state(job_id, chapter, **update)

    def _update_progress(self, job_id: str) -> None:
        totals = self.store.job_segment_totals(job_id)
        total = sum(totals.values())
        done = totals.get("done", 0) + totals.get("failed", 0)
        audio_s = self.store.job_audio_seconds(job_id)
        stats = self._job_stats.get(job_id, {})
        elapsed = time.monotonic() - stats["started_at"] if stats.get("started_at") else 0.0
        busy_s = stats.get("busy_s", 0.0)
        x_realtime = (audio_s / busy_s) if busy_s > 0 else None
        remaining_chars = self.store.pending_chars_sum(job_id)
        chars_per_s = (self._chars_done(job_id) / busy_s) if busy_s > 0 else None
        if chars_per_s:
            eta_s = remaining_chars / chars_per_s
        else:
            eta_s = (remaining_chars / FALLBACK_CHARS_PER_S) / FALLBACK_REALTIME_X
        progress = Progress(segments_total=total, segments_done=done, audio_s=audio_s,
                            elapsed_s=elapsed, eta_s=eta_s, x_realtime=x_realtime)
        self.store.update_job(job_id, progress=progress)
        self._publish_job(job_id)

    def _chars_done(self, job_id: str) -> int:
        totals = self.store.job_segment_totals(job_id)
        # crude proxy: use audio_s * FALLBACK_CHARS_PER_S if no better signal is stored.
        return int(self.store.job_audio_seconds(job_id) * FALLBACK_CHARS_PER_S)

    async def _maybe_finish_chapter(self, job_id: str, chapter: int, settings: JobSettings) -> None:
        """Assemble the chapter once all its segments are finished. Any failure (e.g. output
        folder on an unplugged drive) marks the chapter failed instead of leaving it
        'assembling', which would block the job and the whole queue."""
        if (job_id, chapter) in self._assembling or not self.store.job_exists(job_id):
            return
        self._assembling.add((job_id, chapter))
        try:
            await self._finish_chapter(job_id, chapter, settings)
        except Exception as e:
            log.exception("finishing chapter failed")
            if self.store.job_exists(job_id) and any(
                    c.index == chapter and c.status == "assembling" for c in self.store.get_chapters(job_id)):
                self.store.update_chapter_state(job_id, chapter, status="failed")
                self._log(job_id, "error", f"chapter could not be finished: {e}")
                self._publish_job(job_id, force=True)
                await self._maybe_finish_job(job_id)
        finally:
            self._assembling.discard((job_id, chapter))

    async def _finish_chapter(self, job_id: str, chapter: int, settings: JobSettings) -> None:
        counts = self.store.chapter_segment_counts(job_id, chapter)
        if sum(counts.values()) == 0 or counts.get("pending", 0) or counts.get("running", 0):
            return
        chapters = self.store.get_chapters(job_id)
        cs = next((c for c in chapters if c.index == chapter), None)
        if cs is None or cs.status in ("done", "assembling"):
            return
        self.store.update_chapter_state(job_id, chapter, status="assembling")
        self._publish_job(job_id, force=True)
        rows = self.store.chapter_segments(job_id, chapter)
        seg_list = [(r["processed_wav"], r["pause_after_ms"]) for r in rows if r["status"] == "done"]
        book = self.store.read_book_json(job_id)
        out_dir = self.store.claim_output_dir(job_id, settings, book.get("author"), book.get("title"))
        included = [c for c in chapters if c.include]
        chapter_num = sum(1 for c in included if c.index <= chapter)
        fname = sanitize_filename(f"{chapter_num:02d} - {cs.title or f'Chapter {chapter_num}'}.mp3",
                                   fallback=f"{chapter_num:02d}.mp3")
        out_path = out_dir / fname
        cover = base64.b64decode(book["cover"]) if book.get("cover") else None
        tags = {"title": cs.title or f"Chapter {chapter_num}", "album": book.get("title", ""),
                "artist": book.get("author") or "", "track": chapter_num, "total": len(included)}
        loop = asyncio.get_running_loop()
        duration = 0.0
        if seg_list:
            try:
                duration = await loop.run_in_executor(self._executor, functools.partial(
                    self.services.assemble, seg_list, str(out_path), fmt="mp3",
                    bitrate=settings.bitrate, tags=tags, cover=cover,
                    loudness_lufs=config.LOUDNESS_LUFS,
                ))
            except Exception as e:
                log.exception("chapter assembly failed")
                self.store.update_chapter_state(job_id, chapter, status="failed")
                self._log(job_id, "error", f"chapter {chapter_num} assembly failed: {e}")
                self._publish_job(job_id, force=True)
                await self._maybe_finish_job(job_id)
                return
            self.store.set_chapter_file(job_id, chapter, str(out_path))
        self.store.update_chapter_state(
            job_id, chapter, status="done", duration_s=duration,
            audio_url=f"/api/jobs/{job_id}/chapters/{chapter}/audio" if seg_list else None,
        )
        self.bus.publish(Event(type="chapter", job_id=job_id,
                               data=next(c for c in self.store.get_chapters(job_id)
                                        if c.index == chapter).model_dump()))
        await self._maybe_finish_job(job_id)

    async def _maybe_finish_job(self, job_id: str) -> None:
        if job_id in self._finalizing:
            return
        if self.store.get_job_status(job_id) != JobStatus.running:
            return
        totals = self.store.job_segment_totals(job_id)
        if totals.get("pending", 0) or totals.get("running", 0):
            return
        chapters = self.store.get_chapters(job_id)
        included = [c for c in chapters if c.include]
        if any(c.status not in ("done", "failed", "skipped") for c in included):
            return
        self._finalizing.add(job_id)
        try:
            await self._finalize_job(job_id)
        finally:
            self._finalizing.discard(job_id)

    async def _finalize_job(self, job_id: str) -> None:
        settings = self.store.get_job_settings(job_id)
        book = self.store.read_book_json(job_id)
        chapters = self.store.get_chapters(job_id)
        included = [c for c in chapters if c.include]
        out_dir = self.store.claim_output_dir(job_id, settings, book.get("author"), book.get("title"))
        if settings.output_format in (OutputFormat.m4b, OutputFormat.both):
            entries = [
                (self.store.get_chapter_file(job_id, c.index), c.title or f"Chapter {i + 1}")
                for i, c in enumerate(included) if self.store.get_chapter_file(job_id, c.index)
            ]
            if entries:
                label = f"{book.get('author')} - {book['title']}" if book.get("author") else book["title"]
                m4b_path = out_dir / f"{sanitize_filename(label)}.m4b"
                cover = base64.b64decode(book["cover"]) if book.get("cover") else None
                tags = {"title": book["title"], "album": book["title"],
                        "artist": book.get("author") or "", "track": 1, "total": 1}
                loop = asyncio.get_running_loop()
                try:
                    await loop.run_in_executor(self._executor, functools.partial(
                        self.services.build_m4b, entries, str(m4b_path), tags=tags, cover=cover,
                        bitrate=settings.bitrate,
                    ))
                except Exception as e:
                    log.exception("m4b build failed")
                    self._log(job_id, "error", f"M4B build failed (chapter MP3s are fine): {e}")
        self.store.update_job(job_id, status=JobStatus.done, output_path=str(out_dir))
        self._run_queue = [j for j in self._run_queue if j != job_id]
        self._job_stats.pop(job_id, None)
        self._retries.pop(job_id, None)
        self._publish_job(job_id, force=True)

    async def _run_sample_batch(self, sample_id: str, rows) -> None:
        srow = self.store.get_sample(sample_id)
        job_id = srow["job_id"]
        settings = JobSettings.model_validate_json(srow["settings"])
        if settings.engine == EngineName.local and self.voices.get(settings.voice_id) is None:
            self.store.update_sample(sample_id, status="failed", error=f"voice not found: {settings.voice_id}")
            self._publish_sample(sample_id)
            return
        for r in rows:
            self.store.mark_sample_segment_running(sample_id, r["id"])
        items = [
            SynthesisItem(segment_id=r["id"], text=r["text"], lang=Lang(r["lang"]),
                          out_wav=str(self.store.raw_sample_wav_path(job_id, sample_id, r["id"])))
            for r in rows
        ]
        try:
            results = await self._synthesize(settings, items)
        except Exception as e:
            log.exception("sample synthesis failed")
            for r in rows:
                self.store.mark_sample_segment_failed(sample_id, r["id"])
            reason = "TTS worker crashed" if isinstance(e, WorkerCrashed) else f"TTS error: {e}"
            self.store.update_sample(sample_id, status="failed", error=reason)
            self._publish_sample(sample_id)
            self._wakeup.set()
            return
        for r, res in zip(rows, results):
            self._track_bg(asyncio.ensure_future(
                self._post_process_sample(sample_id, job_id, r, res, settings)))
        self._wakeup.set()

    async def _post_process_sample(self, sample_id: str, job_id: str, seg_row, result: SynthesisResult,
                                    settings: JobSettings) -> None:
        if not result.ok:
            self.store.mark_sample_segment_failed(sample_id, seg_row["id"])
            self.store.refresh_sample_segment_urls(sample_id, job_id)
        else:
            out_wav = self.store.processed_sample_wav_path(job_id, sample_id, seg_row["id"])
            loop = asyncio.get_running_loop()
            try:
                duration = await loop.run_in_executor(
                    self._executor, self.services.process_segment, result.out_wav, str(out_wav),
                    settings.speed,
                )
            except Exception:
                log.exception("sample post-processing failed")
                self.store.mark_sample_segment_failed(sample_id, seg_row["id"])
                self.store.refresh_sample_segment_urls(sample_id, job_id)
                self._publish_sample(sample_id)
                if self.store.sample_segments_pending_count(sample_id) == 0:
                    await self._finish_sample(sample_id, job_id, settings)
                return
            self.store.mark_sample_segment_done(sample_id, seg_row["id"], str(out_wav), duration)
            self.store.refresh_sample_segment_urls(sample_id, job_id)
        self._publish_sample(sample_id)
        if self.store.sample_segments_pending_count(sample_id) == 0:
            await self._finish_sample(sample_id, job_id, settings)

    async def _finish_sample(self, sample_id: str, job_id: str, settings: JobSettings) -> None:
        rows = self.store.sample_segments_ordered(sample_id)
        seg_list = [(r["processed_wav"], r["pause_after_ms"]) for r in rows if r["status"] == "done"]
        if not seg_list:
            self.store.update_sample(sample_id, status="failed", error="all sample segments failed")
            self._publish_sample(sample_id)
            return
        book = self.store.read_book_json(job_id)
        out_path = self.store.sample_output_path(job_id, sample_id)
        tags = {"title": "Sample", "album": book.get("title", ""), "artist": book.get("author") or "",
                "track": 1, "total": 1}
        loop = asyncio.get_running_loop()
        try:
            duration = await loop.run_in_executor(self._executor, functools.partial(
                self.services.assemble, seg_list, str(out_path), fmt="mp3",
                bitrate=settings.bitrate, tags=tags, cover=None, loudness_lufs=config.LOUDNESS_LUFS,
            ))
        except Exception as e:
            log.exception("sample assembly failed")
            self.store.update_sample(sample_id, status="failed", error=f"assembly failed: {e}")
            self._publish_sample(sample_id)
            return
        self.store.update_sample(sample_id, status="done", duration_s=duration,
                                  audio_url=f"/api/jobs/{job_id}/samples/{sample_id}/audio")
        self._publish_sample(sample_id)

    # ------------------------------------------------------------------ events

    def _publish_job(self, job_id: str, force: bool = False) -> None:
        info = self.store.get_job_info(job_id)
        if info is None:
            return
        self.bus.publish(Event(type="job", job_id=job_id, data=info.model_dump(mode="json")),
                          throttle_key=None if force else job_id)

    def _publish_sample(self, sample_id: str) -> None:
        row = self.store.get_sample(sample_id)
        if row is None:
            return
        info = self.store.sample_to_info(row)
        self.bus.publish(Event(type="sample", job_id=info.job_id, data=info.model_dump(mode="json")))

    def _log(self, job_id: str, level: str, message: str) -> None:
        self.bus.publish(Event(type="log", job_id=job_id, data={"level": level, "message": message}))
