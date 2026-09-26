"""Shared data models and interfaces between modules.

OWNED BY THE LEAD. Implementer agents must not change this file; ask for changes instead.

Data flow:
  ingest.parse_book(path)             -> Book
  text.prepare_chapter(chapter, ...)  -> list[Segment]
  pipeline.Scheduler                  -> TTSWorker.synthesize(batch) -> wavs on disk
  audio.post                          -> per-segment wav (trimmed, speed) -> chapter mp3 / book m4b
  api (FastAPI + SSE)                 <- Event stream -> frontend
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal, Optional, Protocol, Sequence, runtime_checkable

import numpy as np
from pydantic import BaseModel, Field


class Lang(str, Enum):
    ru = "ru"
    en = "en"


# ----------------------------------------------------------------------------- ingest

class Chapter(BaseModel):
    index: int                        # 0-based, in reading order
    title: str                        # "" if the source has none (UI shows "Chapter N")
    paragraphs: list[str]             # clean plain text, one paragraph per item, no headings
    include: bool = True              # user may exclude (front matter, TOC, notes...)
    kind: Literal["body", "front", "back", "notes", "toc"] = "body"  # parser's best guess

    @property
    def chars(self) -> int:
        return sum(len(p) for p in self.paragraphs)


class Book(BaseModel):
    title: str
    author: Optional[str] = None
    lang: Optional[Lang] = None       # detected by ingest (Cyrillic ratio) — may be None
    source_path: str
    source_format: str                # "epub", "fb2", "pdf", ... (lowercase, no dot)
    cover: Optional[bytes] = None     # raw image bytes (jpeg/png) if the source has one
    cover_mime: Optional[str] = None
    chapters: list[Chapter]
    warnings: list[str] = Field(default_factory=list)  # e.g. "PDF had no text layer, OCR used"


class IngestError(Exception):
    """Unreadable / DRM / empty / unsupported file. Message is shown to the user."""


# ingest/__init__.py must provide:
#   SUPPORTED_EXTENSIONS: list[str]            e.g. [".epub", ".fb2", ".fb2.zip", ...]
#   def parse_book(path: Path) -> Book         raises IngestError


# ----------------------------------------------------------------------------- text

class Segment(BaseModel):
    """One TTS unit: 1-3 sentences, <= config.SEGMENT_MAX_CHARS of normalized text."""
    id: str                           # f"c{chapter:03d}s{index:04d}" (stable, sortable)
    chapter: int
    index: int                        # position within chapter
    source: str                       # original text (for display / QA fallback)
    text: str                         # normalized text actually sent to TTS
    lang: Lang
    pause_after_ms: int               # silence to insert after this segment
    is_title: bool = False            # chapter heading segment


# text/__init__.py must provide:
#   def detect_lang(text: str) -> Lang
#   def normalize(text: str, lang: Lang) -> str                  numbers, abbreviations, ё, symbols
#   def prepare_chapter(chapter: Chapter, lang: Lang, *, read_title: bool = True,
#                       skip_footnotes: bool = True,
#                       pause_sentence_ms: int = config.PAUSE_SENTENCE_MS,
#                       pause_paragraph_ms: int = config.PAUSE_PARAGRAPH_MS) -> list[Segment]
#        Paragraphs detected as the other language get that Segment.lang (mixed-language books).
#        Title segment (is_title=True) gets config.PAUSE_CHAPTER_TITLE_MS.
#   def compare_form(text: str, lang: Lang) -> str               canonical form for QA scoring:
#        lowercase, ё->е, digits -> words, punctuation stripped, whitespace collapsed.
#        Applied to BOTH the source text and the ASR transcript.


# ----------------------------------------------------------------------------- voices

class Voice(BaseModel):
    id: str
    name: str
    lang: Lang
    gender: Optional[Literal["male", "female"]] = None
    ref_audio: str                    # absolute path to reference wav
    ref_text: str                     # exact transcript of ref_audio
    description: str = ""
    builtin: bool = False


# ----------------------------------------------------------------------------- engine (runs inside the GPU worker process)

class SynthesisParams(BaseModel):
    temperature: float = 0.8          # "expressiveness" in the UI (0.5 flat .. 1.0 lively)
    top_p: float = 1.0
    repetition_penalty: float = 1.05


class EngineOutput(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    audio: np.ndarray                 # float32 mono at Engine.sample_rate
    tokens: int
    hit_token_cap: bool               # generation stopped at max_tokens -> suspect, regenerate


@runtime_checkable
class Engine(Protocol):
    sample_rate: int

    def load(self) -> None: ...
    def unload(self) -> None: ...
    def synthesize(self, texts: Sequence[str], lang: Lang, voice: Voice,
                   params: SynthesisParams) -> list[EngineOutput]:
        """One batched call. len(output) == len(texts), same order.
        Must cap max_tokens per text via config.token_budget and set MLX memory limits."""
        ...


@runtime_checkable
class Verifier(Protocol):
    """Offline ASR check (Whisper). Fake implementation returns cer=0."""
    def check(self, audio: np.ndarray, sample_rate: int, text: str, lang: Lang) -> tuple[float, str]:
        """Returns (character error rate on compare_form of both sides, transcript)."""
        ...


# ----------------------------------------------------------------------------- worker (main process <-> GPU worker process)

class SynthesisItem(BaseModel):
    segment_id: str
    text: str
    lang: Lang
    out_wav: str                      # where the worker writes the raw 24 kHz wav


class SynthesisResult(BaseModel):
    segment_id: str
    ok: bool
    out_wav: Optional[str] = None
    duration_s: float = 0.0
    attempts: int = 1
    cer: Optional[float] = None       # None when QA disabled
    transcript: Optional[str] = None
    error: Optional[str] = None


class WorkerStatus(BaseModel):
    state: Literal["stopped", "starting", "idle", "busy", "restarting", "failed"]
    footprint_gb: float = 0.0
    system_available_gb: float = 0.0
    restarts: int = 0
    model: Optional[str] = None
    message: Optional[str] = None


class TTSWorker(Protocol):
    """Main-process handle to the GPU worker. ProcessWorker (real, supervised subprocess with
    memory guard) and InProcessWorker(FakeEngine) (tests) both implement this."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def status(self) -> WorkerStatus: ...

    async def synthesize(self, items: list[SynthesisItem], voice: Voice, params: SynthesisParams,
                         qa: bool) -> list[SynthesisResult]:
        """Batch of <= config.BATCH_SIZE items, same lang. With qa=True the worker transcribes
        each output and regenerates failures (cer > QA_MAX_CER or hit_token_cap) up to
        QA_MAX_ATTEMPTS, keeping the best attempt. Never raises for per-item failures
        (ok=False); raises WorkerCrashed if the process died / was killed by the memory guard
        (the scheduler then retries the batch after restart)."""
        ...

    async def design_voice(self, description: str, lang: Lang, text: str, out_wav: str) -> float:
        """VoiceDesign model: render `text` in a voice described by `description` to out_wav.
        Returns duration. Worker swaps models (never both resident). Only called when idle."""
        ...


class WorkerCrashed(Exception):
    pass


# ----------------------------------------------------------------------------- jobs

class OutputFormat(str, Enum):
    mp3 = "mp3"                       # one mp3 per chapter
    m4b = "m4b"                       # single audiobook with chapter markers
    both = "both"


class JobSettings(BaseModel):
    voice_id: str = "ru_male"
    lang: Optional[Lang] = None       # None = use Book.lang (detected)
    speed: float = Field(1.0, ge=0.7, le=1.5)     # post-processing tempo (ffmpeg atempo)
    params: SynthesisParams = SynthesisParams()
    output_dir: str = ""              # parent folder; book goes in <output_dir>/<Author - Title>/
    output_format: OutputFormat = OutputFormat.mp3
    bitrate: str = "96k"
    qa: bool = True
    read_titles: bool = True
    skip_footnotes: bool = True
    pause_paragraph_ms: int = 700
    pause_sentence_ms: int = 250


class JobStatus(str, Enum):
    parsed = "parsed"                 # book parsed, user reviewing chapters / settings / samples
    queued = "queued"
    running = "running"
    paused = "paused"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class ChapterState(BaseModel):
    index: int
    title: str
    include: bool
    chars: int
    segments_total: int = 0
    segments_done: int = 0
    status: Literal["pending", "running", "assembling", "done", "failed", "skipped"] = "pending"
    audio_url: Optional[str] = None   # final chapter file once assembled
    duration_s: float = 0.0


class Progress(BaseModel):
    segments_total: int = 0
    segments_done: int = 0
    audio_s: float = 0.0              # seconds of audio produced so far
    elapsed_s: float = 0.0
    eta_s: Optional[float] = None
    x_realtime: Optional[float] = None


class JobInfo(BaseModel):
    id: str
    status: JobStatus
    title: str
    author: Optional[str]
    lang: Optional[Lang]
    source_format: str
    has_cover: bool
    created_at: float
    settings: JobSettings
    chapters: list[ChapterState]
    progress: Progress
    output_path: Optional[str] = None  # the book folder
    warnings: list[str] = []
    error: Optional[str] = None
    sample_approved: bool = False      # a sample was rendered+approved with the CURRENT settings


class SampleRequest(BaseModel):
    chapter: Optional[int] = None      # default: middle included chapter
    offset: Optional[float] = None     # 0..1 position within chapter; default 0.5
    seconds: int = Field(60, ge=10, le=300)  # approx target length (by chars: ~14 chars/s)
    text: Optional[str] = None         # custom text instead of book text


class SampleInfo(BaseModel):
    id: str
    job_id: str
    status: Literal["queued", "running", "done", "failed"]
    settings: JobSettings              # settings snapshot used to render it
    text: str                          # source text of the sample
    segments: list[str] = []           # segment audio urls, in order, as they complete
    audio_url: Optional[str] = None    # assembled sample (mp3) once done
    duration_s: float = 0.0
    created_at: float
    error: Optional[str] = None


# ----------------------------------------------------------------------------- events (SSE)

class Event(BaseModel):
    """Sent over GET /api/jobs/{id}/events as `event: <type>\\ndata: <json>`."""
    type: Literal["job", "segment", "chapter", "sample", "voice", "worker", "log"]
    job_id: Optional[str] = None
    data: dict
    # job:     JobInfo.model_dump() (full snapshot; sent on subscribe and on every status change,
    #          and throttled progress updates at most every 1 s)
    # segment: {segment_id, chapter, index, url, duration_s, cer}
    # chapter: ChapterState.model_dump()
    # sample:  SampleInfo.model_dump()
    # voice:   {voice: Voice, status: "ready"|"failed", error?}
    # worker:  WorkerStatus.model_dump()
    # log:     {level: "info"|"warning"|"error", message}
