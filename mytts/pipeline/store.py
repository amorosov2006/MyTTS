"""SQLite persistence for jobs/chapters/segments/samples, plus job workdir/output paths.

One sqlite3 connection, opened in WAL mode. Every method is synchronous and is only
ever called from the asyncio event-loop thread (the API handlers and the scheduler's
dispatcher task) — a `threading.Lock` guards it anyway as cheap insurance.

Job workdir: ``config.DATA_DIR/jobs/<job_id>/``
  upload.<ext>            copy of the uploaded file
  book.json                parsed Book, cover as base64
  raw/<segment_id>.wav      raw TTS output
  seg/<segment_id>.wav      post-processed (trimmed, speed-adjusted)
  chapters/<NN>.mp3         assembled chapter (copied to the output dir too)
  samples/<sample_id>/...   raw/, seg/, sample.mp3
"""
from __future__ import annotations

import base64
import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from mytts import config
from mytts.contracts import (
    Book, Chapter, ChapterState, JobInfo, JobSettings, JobStatus, Lang, Progress,
    SampleInfo, Segment,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT,
    lang TEXT,
    source_format TEXT NOT NULL,
    has_cover INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    settings TEXT NOT NULL,
    settings_hash TEXT NOT NULL,
    chapters TEXT NOT NULL,
    progress TEXT NOT NULL,
    output_path TEXT,
    warnings TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    sample_approved INTEGER NOT NULL DEFAULT 0,
    workdir TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS segments (
    job_id TEXT NOT NULL,
    id TEXT NOT NULL,
    chapter INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    source TEXT NOT NULL,
    text TEXT NOT NULL,
    lang TEXT NOT NULL,
    pause_after_ms INTEGER NOT NULL,
    is_title INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    processed_wav TEXT,
    duration_s REAL NOT NULL DEFAULT 0,
    cer REAL,
    transcript TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    PRIMARY KEY (job_id, id)
);
CREATE INDEX IF NOT EXISTS idx_segments_pending ON segments (job_id, status, chapter, idx);

CREATE TABLE IF NOT EXISTS samples (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,
    settings TEXT NOT NULL,
    settings_hash TEXT NOT NULL,
    text TEXT NOT NULL,
    segments TEXT NOT NULL DEFAULT '[]',
    audio_url TEXT,
    duration_s REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS chapter_files (
    job_id TEXT NOT NULL,
    chapter INTEGER NOT NULL,
    path TEXT NOT NULL,
    PRIMARY KEY (job_id, chapter)
);

CREATE TABLE IF NOT EXISTS sample_segments (
    sample_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    lang TEXT NOT NULL,
    pause_after_ms INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    processed_wav TEXT,
    duration_s REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (sample_id, id)
);
CREATE INDEX IF NOT EXISTS idx_sample_segments_pending ON sample_segments (status, sample_id, idx);
"""

# Fields whose change invalidates an approved sample (PLAN §9 / API.md).
AUDIBLE_SETTINGS_FIELDS = (
    "voice_id", "lang", "speed", "params", "pause_sentence_ms", "pause_paragraph_ms",
    "skip_footnotes", "qa",
)

_UNSAFE_CHARS = re.compile(r'[\/:*?"<>|\x00-\x1f]')


def sanitize_filename(name: str, fallback: str = "untitled") -> str:
    name = _UNSAFE_CHARS.sub("_", name).strip(" .")
    name = re.sub(r"\s+", " ", name)
    return name[:150] or fallback


def audible_settings_hash(settings: JobSettings) -> str:
    payload = {k: _jsonable(getattr(settings, k)) for k in AUDIBLE_SETTINGS_FIELDS}
    return json.dumps(payload, sort_keys=True, default=str)


def _jsonable(v: Any) -> Any:
    if hasattr(v, "model_dump"):
        return v.model_dump()
    if hasattr(v, "value"):  # Enum
        return v.value
    return v


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else (config.DATA_DIR / "mytts.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ paths

    def job_workdir(self, job_id: str) -> Path:
        d = config.DATA_DIR / "jobs" / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def output_book_dir(self, settings: JobSettings, author: Optional[str], title: str) -> Path:
        base = Path(settings.output_dir) if settings.output_dir else config.DEFAULT_OUTPUT_DIR
        label = f"{author} - {title}" if author else title
        d = base / sanitize_filename(label)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def claim_output_dir(self, job_id: str, settings: JobSettings, author: Optional[str],
                         title: str) -> Path:
        """The job's own book folder: "<Author - Title>", or "... (2)", "(3)" when that folder
        already holds another job's (or anyone's) files — never overwrite a previous conversion.
        Ownership is recorded in a hidden .mytts-job marker; the path is stored on the job."""
        info = self.get_job_info(job_id)
        if info is not None and info.output_path:
            d = Path(info.output_path)
            d.mkdir(parents=True, exist_ok=True)
            return d
        first = self.output_book_dir(settings, author, title)
        for n in range(1, 1000):
            d = first if n == 1 else first.with_name(f"{first.name} ({n})")
            marker = d / ".mytts-job"
            owner = marker.read_text().strip() if marker.exists() else None
            if owner == job_id or (owner is None and (not d.exists() or not any(d.iterdir()))):
                d.mkdir(parents=True, exist_ok=True)
                marker.write_text(job_id)
                self.update_job(job_id, output_path=str(d))
                return d
        raise RuntimeError("no free output folder name")

    # ------------------------------------------------------------------ jobs

    def create_job(self, book: Book, settings: JobSettings, source_path: Path) -> str:
        job_id = new_id("job")
        workdir = self.job_workdir(job_id)
        ext = Path(book.source_path).suffix or f".{book.source_format}"
        upload_copy = workdir / f"upload{ext}"
        upload_copy.write_bytes(Path(source_path).read_bytes())
        self._write_book_json(workdir, book)

        chapters = [
            ChapterState(index=c.index, title=c.title, include=c.include, chars=c.chars,
                         kind=c.kind).model_dump()
            for c in book.chapters
        ]
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO jobs (id, status, title, author, lang, source_format, has_cover,"
                " created_at, settings, settings_hash, chapters, progress, output_path, warnings,"
                " error, sample_approved, workdir) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id, JobStatus.parsed.value, book.title, book.author,
                    book.lang.value if book.lang else None, book.source_format,
                    int(book.cover is not None), now, settings.model_dump_json(),
                    audible_settings_hash(settings), json.dumps(chapters),
                    Progress().model_dump_json(), None, json.dumps(book.warnings), None, 0,
                    str(workdir),
                ),
            )
        return job_id

    def _write_book_json(self, workdir: Path, book: Book) -> None:
        data = book.model_dump(mode="json", exclude={"cover"})
        data["cover"] = base64.b64encode(book.cover).decode("ascii") if book.cover else None
        (workdir / "book.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def read_book_json(self, job_id: str) -> dict:
        return json.loads((self.job_workdir(job_id) / "book.json").read_text(encoding="utf-8"))

    def _row(self, job_id: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def job_exists(self, job_id: str) -> bool:
        return self._row(job_id) is not None

    def get_job_status(self, job_id: str) -> Optional[JobStatus]:
        row = self._row(job_id)
        return JobStatus(row["status"]) if row else None

    def get_job_settings(self, job_id: str) -> Optional[JobSettings]:
        row = self._row(job_id)
        return JobSettings.model_validate_json(row["settings"]) if row else None

    def get_job_workdir_str(self, job_id: str) -> Optional[str]:
        row = self._row(job_id)
        return row["workdir"] if row else None

    def get_job_info(self, job_id: str) -> Optional[JobInfo]:
        row = self._row(job_id)
        return self._to_job_info(row) if row else None

    def list_jobs(self) -> list[JobInfo]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [self._to_job_info(r) for r in rows]

    def _to_job_info(self, row: sqlite3.Row) -> JobInfo:
        return JobInfo(
            id=row["id"],
            status=JobStatus(row["status"]),
            title=row["title"],
            author=row["author"],
            lang=Lang(row["lang"]) if row["lang"] else None,
            source_format=row["source_format"],
            has_cover=bool(row["has_cover"]),
            created_at=row["created_at"],
            settings=JobSettings.model_validate_json(row["settings"]),
            chapters=[ChapterState(**c) for c in json.loads(row["chapters"])],
            progress=Progress.model_validate_json(row["progress"]),
            output_path=row["output_path"],
            warnings=json.loads(row["warnings"]),
            error=row["error"],
            sample_approved=bool(row["sample_approved"]),
        )

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            if k in ("chapters",) and not isinstance(v, str):
                v = json.dumps(v)
            elif k == "warnings" and not isinstance(v, str):
                v = json.dumps(v)
            elif k == "settings" and hasattr(v, "model_dump_json"):
                v = v.model_dump_json()
            elif k == "progress" and hasattr(v, "model_dump_json"):
                v = v.model_dump_json()
            elif k == "status" and hasattr(v, "value"):
                v = v.value
            elif k == "lang" and hasattr(v, "value"):
                v = v.value
            elif k == "sample_approved":
                v = int(bool(v))
            cols.append(f"{k}=?")
            vals.append(v)
        vals.append(job_id)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id=?", vals)

    def set_settings(self, job_id: str, settings: JobSettings) -> None:
        self.update_job(job_id, settings=settings, settings_hash=audible_settings_hash(settings))

    def get_settings_hash(self, job_id: str) -> Optional[str]:
        row = self._row(job_id)
        return row["settings_hash"] if row else None

    def get_chapters(self, job_id: str) -> list[ChapterState]:
        row = self._row(job_id)
        return [ChapterState(**c) for c in json.loads(row["chapters"])] if row else []

    def update_chapters_meta(self, job_id: str, updates: list[dict]) -> None:
        """Apply {"index", "title"?, "include"?} patches (PATCH /chapters)."""
        chapters = self.get_chapters(job_id)
        by_index = {c.index: c for c in chapters}
        for u in updates:
            c = by_index.get(u["index"])
            if c is None:
                continue
            if "title" in u and u["title"] is not None:
                c.title = u["title"]
            if "include" in u and u["include"] is not None:
                c.include = u["include"]
        self.update_job(job_id, chapters=[c.model_dump() for c in chapters])

    def update_chapter_state(self, job_id: str, index: int, **fields: Any) -> None:
        chapters = self.get_chapters(job_id)
        for c in chapters:
            if c.index == index:
                for k, v in fields.items():
                    setattr(c, k, v)
                break
        self.update_job(job_id, chapters=[c.model_dump() for c in chapters])

    def delete_job(self, job_id: str) -> None:
        import shutil
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self._conn.execute("DELETE FROM segments WHERE job_id=?", (job_id,))
            sids = [r["id"] for r in self._conn.execute(
                "SELECT id FROM samples WHERE job_id=?", (job_id,)).fetchall()]
            self._conn.execute("DELETE FROM samples WHERE job_id=?", (job_id,))
            for sid in sids:
                self._conn.execute("DELETE FROM sample_segments WHERE sample_id=?", (sid,))
        shutil.rmtree(config.DATA_DIR / "jobs" / job_id, ignore_errors=True)

    # ------------------------------------------------------------------ segments

    def add_segments(self, job_id: str, chapter: int, segments: list[Segment]) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO segments (job_id, id, chapter, idx, source, text, lang,"
                " pause_after_ms, is_title, status) VALUES (?,?,?,?,?,?,?,?,?,'pending')",
                [
                    (job_id, s.id, chapter, s.index, s.source, s.text, s.lang.value,
                     s.pause_after_ms, int(s.is_title))
                    for s in segments
                ],
            )
        self.update_chapter_state(job_id, chapter, segments_total=len(segments), segments_done=0)

    def raw_wav_path(self, job_id: str, segment_id: str) -> Path:
        d = self.job_workdir(job_id) / "raw"
        d.mkdir(exist_ok=True)
        return d / f"{segment_id}.wav"

    def processed_wav_path(self, job_id: str, segment_id: str) -> Path:
        d = self.job_workdir(job_id) / "seg"
        d.mkdir(exist_ok=True)
        return d / f"{segment_id}.wav"

    def next_pending_segments(self, job_id: str, limit: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM segments WHERE job_id=? AND status='pending'"
                " ORDER BY chapter ASC, idx ASC LIMIT ?",
                (job_id, limit),
            ).fetchall()

    def mark_segment_running(self, job_id: str, segment_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE segments SET status='running', attempts=attempts+1 WHERE job_id=? AND id=?",
                (job_id, segment_id),
            )

    def mark_segment_pending(self, job_id: str, segment_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE segments SET status='pending' WHERE job_id=? AND id=?", (job_id, segment_id)
            )

    def mark_segment_done(self, job_id: str, segment_id: str, processed_wav: str, duration_s: float,
                           cer: Optional[float], transcript: Optional[str], attempts: int = 1) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE segments SET status='done', processed_wav=?, duration_s=?, cer=?,"
                " transcript=?, attempts=? WHERE job_id=? AND id=?",
                (processed_wav, duration_s, cer, transcript, attempts, job_id, segment_id),
            )

    def mark_segment_failed(self, job_id: str, segment_id: str, error: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE segments SET status='failed', error=? WHERE job_id=? AND id=?",
                (error, job_id, segment_id),
            )

    def get_segment(self, job_id: str, segment_id: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM segments WHERE job_id=? AND id=?", (job_id, segment_id)
            ).fetchone()

    def chapter_segments(self, job_id: str, chapter: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM segments WHERE job_id=? AND chapter=? ORDER BY idx ASC",
                (job_id, chapter),
            ).fetchall()

    def chapter_segment_counts(self, job_id: str, chapter: int) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) n FROM segments WHERE job_id=? AND chapter=? GROUP BY status",
                (job_id, chapter),
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def job_segment_totals(self, job_id: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) n FROM segments WHERE job_id=? GROUP BY status", (job_id,)
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def job_audio_seconds(self, job_id: str) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(duration_s),0) s FROM segments WHERE job_id=? AND status='done'",
                (job_id,),
            ).fetchone()
        return float(row["s"])

    def pending_chars_sum(self, job_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(LENGTH(text)),0) n FROM segments"
                " WHERE job_id=? AND status IN ('pending','running')",
                (job_id,),
            ).fetchone()
        return int(row["n"])

    def reset_running_segments(self, job_id: str, exclude: frozenset = frozenset()) -> None:
        """After a crash/restart: work that was in flight is lost, so re-queue it. `exclude`:
        segments still genuinely in flight in this process (pause→resume) must not be redone."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT id FROM segments WHERE job_id=? AND status='running'", (job_id,)).fetchall()
            for r in rows:
                if r["id"] not in exclude:
                    self._conn.execute("UPDATE segments SET status='pending' WHERE job_id=? AND id=?",
                                       (job_id, r["id"]))
            self._conn.execute(
                "UPDATE sample_segments SET status='pending' WHERE job_id=? AND status='running'",
                (job_id,),
            )

    def set_chapter_file(self, job_id: str, chapter: int, path: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO chapter_files (job_id, chapter, path) VALUES (?,?,?)",
                (job_id, chapter, path),
            )

    def get_chapter_file(self, job_id: str, chapter: int) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT path FROM chapter_files WHERE job_id=? AND chapter=?", (job_id, chapter)
            ).fetchone()
        return row["path"] if row else None

    # ------------------------------------------------------------------ samples

    def create_sample(self, sample_id: str, job_id: str, settings: JobSettings, text: str,
                       segments: list[Segment]) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO samples (id, job_id, status, settings, settings_hash, text, segments,"
                " created_at) VALUES (?,?,?,?,?,?,?,?)",
                (sample_id, job_id, "queued", settings.model_dump_json(),
                 audible_settings_hash(settings), text, "[]", now),
            )
            self._conn.executemany(
                "INSERT INTO sample_segments (sample_id, job_id, id, idx, text, lang,"
                " pause_after_ms, status) VALUES (?,?,?,?,?,?,?,'pending')",
                [
                    (sample_id, job_id, s.id, s.index, s.text, s.lang.value, s.pause_after_ms)
                    for s in segments
                ],
            )

    def get_sample(self, sample_id: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute("SELECT * FROM samples WHERE id=?", (sample_id,)).fetchone()

    def list_samples(self, job_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM samples WHERE job_id=? ORDER BY created_at ASC", (job_id,)
            ).fetchall()

    def sample_to_info(self, row: sqlite3.Row) -> SampleInfo:
        return SampleInfo(
            id=row["id"], job_id=row["job_id"], status=row["status"],
            settings=JobSettings.model_validate_json(row["settings"]),
            text=row["text"], segments=json.loads(row["segments"]),
            audio_url=row["audio_url"], duration_s=row["duration_s"],
            created_at=row["created_at"], error=row["error"],
        )

    def update_sample(self, sample_id: str, **fields: Any) -> None:
        cols, vals = [], []
        for k, v in fields.items():
            if k == "segments" and not isinstance(v, str):
                v = json.dumps(v)
            cols.append(f"{k}=?")
            vals.append(v)
        vals.append(sample_id)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE samples SET {', '.join(cols)} WHERE id=?", vals)

    def append_sample_segment_url(self, sample_id: str, url: str) -> None:
        row = self.get_sample(sample_id)
        urls = json.loads(row["segments"]) if row else []
        urls.append(url)
        self.update_sample(sample_id, segments=urls)

    def raw_sample_wav_path(self, job_id: str, sample_id: str, segment_id: str) -> Path:
        d = self.job_workdir(job_id) / "samples" / sample_id / "raw"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{segment_id}.wav"

    def processed_sample_wav_path(self, job_id: str, sample_id: str, segment_id: str) -> Path:
        d = self.job_workdir(job_id) / "samples" / sample_id / "seg"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{segment_id}.wav"

    def sample_output_path(self, job_id: str, sample_id: str) -> Path:
        d = self.job_workdir(job_id) / "samples" / sample_id
        d.mkdir(parents=True, exist_ok=True)
        return d / "sample.mp3"

    def reset_running_sample_segments(self) -> None:
        """At startup: sample segments left 'running' by a crash go back to pending."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sample_segments SET status='pending' WHERE status='running' AND sample_id IN"
                " (SELECT id FROM samples WHERE status IN ('queued','running'))")

    def oldest_pending_sample(self) -> Optional[sqlite3.Row]:
        """The oldest sample (any job) that still has pending segment work."""
        with self._lock:
            return self._conn.execute(
                "SELECT s.* FROM samples s WHERE s.status IN ('queued','running') AND EXISTS"
                " (SELECT 1 FROM sample_segments ss WHERE ss.sample_id=s.id AND ss.status='pending')"
                " ORDER BY s.created_at ASC LIMIT 1"
            ).fetchone()

    def next_pending_sample_segments_for(self, sample_id: str, limit: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM sample_segments WHERE sample_id=? AND status='pending'"
                " ORDER BY idx ASC LIMIT ?",
                (sample_id, limit),
            ).fetchall()

    def mark_sample_segment_running(self, sample_id: str, segment_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sample_segments SET status='running' WHERE sample_id=? AND id=?",
                (sample_id, segment_id),
            )

    def mark_sample_segment_done(self, sample_id: str, segment_id: str, processed_wav: str,
                                  duration_s: float) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sample_segments SET status='done', processed_wav=?, duration_s=?"
                " WHERE sample_id=? AND id=?",
                (processed_wav, duration_s, sample_id, segment_id),
            )

    def mark_sample_segment_failed(self, sample_id: str, segment_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sample_segments SET status='failed' WHERE sample_id=? AND id=?",
                (sample_id, segment_id),
            )

    def sample_segments_pending_count(self, sample_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) n FROM sample_segments WHERE sample_id=? AND status IN"
                " ('pending','running')",
                (sample_id,),
            ).fetchone()
        return row["n"]

    def sample_segments_ordered(self, sample_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM sample_segments WHERE sample_id=? ORDER BY idx ASC", (sample_id,)
            ).fetchall()
