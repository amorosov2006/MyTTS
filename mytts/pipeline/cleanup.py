"""Working-file hygiene. Intermediate audio (per-segment WAVs) is only needed until a chapter's
MP3 is written; without cleanup a single 10-hour book leaves ~3 GB behind in DATA_DIR/jobs.

Never touches the user's output folder (finished MP3/M4B) or a job's upload/book.json (needed
for "Convert again").
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from mytts import config
from mytts.contracts import JobStatus

log = logging.getLogger("mytts.cleanup")

KEEP_SAMPLES_PER_JOB = 5
SAMPLE_WAV_GRACE_S = 600      # finished samples keep segment WAVs this long (live playback)
PREVIEW_CACHE_MAX_FILES = 200
FINISHED = (JobStatus.done, JobStatus.cancelled)


def remove(path) -> None:
    """Delete a file or directory tree; never raises."""
    if not path:
        return
    p = Path(path)
    try:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    except OSError as e:
        log.warning("could not remove %s: %s", p, e)


def remove_many(paths) -> None:
    for p in paths:
        remove(p)


def jobs_root() -> Path:
    return config.DATA_DIR / "jobs"


def prune_finished_job(job_id: str) -> None:
    """Done/cancelled job: drop every per-segment WAV (chapter files are already written)."""
    d = jobs_root() / job_id
    remove(d / "raw")
    remove(d / "seg")


def prune_samples(store, job_id: str, keep: int = KEEP_SAMPLES_PER_JOB) -> None:
    """Keep the newest `keep` samples of a job; finished ones keep only sample.mp3 after a
    grace period (their segment WAVs serve live playback while rendering)."""
    samples = store.list_samples(job_id)  # oldest first
    for row in samples[:-keep] if keep else samples:
        if row["status"] not in ("queued", "running"):
            store.delete_sample(row["id"])
            remove(jobs_root() / job_id / "samples" / row["id"])
    now = time.time()
    for row in samples[-keep:] if keep else []:
        if row["status"] in ("done", "failed") and now - row["created_at"] > SAMPLE_WAV_GRACE_S:
            d = jobs_root() / job_id / "samples" / row["id"]
            remove(d / "raw")
            remove(d / "seg")


def prune_preview_cache(max_files: int = PREVIEW_CACHE_MAX_FILES) -> None:
    d = config.DATA_DIR / "gemini_previews"
    if not d.is_dir():
        return
    files = sorted(d.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[max_files:]:
        remove(p)


def startup_cleanup(store) -> None:
    """Remove what a crash or an older version left behind."""
    known = set()
    for info in store.list_jobs():
        known.add(info.id)
        if info.status in FINISHED:
            prune_finished_job(info.id)
        else:  # raw WAVs of already processed segments are never read again
            for path in store.raw_wavs_of_done_segments(info.id):
                remove(path)
        prune_samples(store, info.id)
    root = jobs_root()
    if root.is_dir():
        for d in root.iterdir():
            if d.is_dir() and d.name not in known:  # job deleted from the DB (or a test leftover)
                remove(d)
    prune_preview_cache()
