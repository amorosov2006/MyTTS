"""Fake stand-ins for the ingest/text/audio.post/memguard modules other agents are building.

Not the real thing — just enough behavior for the scheduler/store/API tests in this worktree
to exercise ordering, resume, pause/cancel and settings-reset without a GPU or real parsers.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

from mytts.config import SAMPLE_RATE
from mytts.contracts import Book, Chapter, IngestError, Lang, Segment

CHARS_PER_S = 14.0
_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")


def fake_parse_book(path) -> Book:
    """Toy format: chapters separated by a "%%CHAPTER%%" line; an optional first
    "# Title" line names the chapter; paragraphs are separated by blank lines."""
    path = Path(path)
    if path.suffix.lower() != ".txt":
        raise IngestError(f"unsupported format for the fake parser: {path.suffix}")
    raw = path.read_text(encoding="utf-8", errors="replace")
    if not raw.strip():
        raise IngestError("empty file")
    chapters = []
    for i, block in enumerate(raw.split("%%CHAPTER%%")):
        lines = block.strip("\n").split("\n")
        title = ""
        if lines and lines[0].startswith("# "):
            title = lines[0][2:].strip()
            lines = lines[1:]
        body = "\n".join(lines).strip()
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
        chapters.append(Chapter(index=i, title=title, paragraphs=paragraphs or ["(empty chapter)"]))
    lang = Lang.ru if len(_CYRILLIC.findall(raw)) > 5 else Lang.en
    return Book(title=path.stem, author="Test Author", lang=lang, source_path=str(path),
                source_format="txt", chapters=chapters, cover=None)


def fake_detect_lang(text: str) -> Lang:
    return Lang.ru if _CYRILLIC.search(text) else Lang.en


def fake_prepare_chapter(chapter: Chapter, lang: Lang, *, read_title: bool = True,
                         skip_footnotes: bool = True, pause_sentence_ms: int = 250,
                         pause_paragraph_ms: int = 700, target_chars: int = 220,
                         max_chars: int = 350, join_paragraphs: bool = False) -> list[Segment]:
    segments: list[Segment] = []
    idx = 0
    ci = max(chapter.index, 0)
    if read_title and chapter.title:
        segments.append(Segment(id=f"c{ci:03d}s{idx:04d}", chapter=chapter.index, index=idx,
                                source=chapter.title, text=chapter.title, lang=lang,
                                pause_after_ms=1200, is_title=True))
        idx += 1
    for p in chapter.paragraphs:
        if skip_footnotes and p.startswith("[note]"):
            continue
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", p) if s.strip()] or [p]
        for si, s in enumerate(sentences):
            pause = pause_paragraph_ms if si == len(sentences) - 1 else pause_sentence_ms
            segments.append(Segment(id=f"c{ci:03d}s{idx:04d}", chapter=chapter.index, index=idx,
                                    source=s, text=s, lang=lang, pause_after_ms=pause))
            idx += 1
    return segments


def fake_process_segment(raw_wav: str, out_wav: str, speed: float = 1.0) -> float:
    data, sr = sf.read(raw_wav, dtype="float32")
    nz = np.where(np.abs(data) > 0.01)[0]
    if len(nz):
        data = data[nz[0]:nz[-1] + 1]
    if speed != 1.0 and len(data) > 1:
        n = max(1, int(len(data) / speed))
        data = data[np.linspace(0, len(data) - 1, n).astype(np.int64)]
    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    sf.write(out_wav, data, sr)
    return len(data) / sr


def fake_assemble(segments, out_path, *, fmt: str = "mp3", bitrate: str = "96k",
                  tags: Optional[dict] = None, cover: Optional[bytes] = None,
                  loudness_lufs: float = -18.0) -> float:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    chunks, sr = [], SAMPLE_RATE
    for wav_path, pause_ms in segments:
        data, sr = sf.read(wav_path, dtype="float32")
        chunks.append(data)
        if pause_ms:
            chunks.append(np.zeros(int(sr * pause_ms / 1000), dtype=np.float32))
    audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
    fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        sf.write(tmp_wav, audio, sr)
        subprocess.run(["ffmpeg", "-y", "-i", tmp_wav, "-codec:a", "libmp3lame", "-b:a", bitrate,
                       str(out_path)], check=True, capture_output=True)
    finally:
        os.unlink(tmp_wav)
    _tag(out_path, tags, cover)
    return len(audio) / sr


def _tag(path: Path, tags: Optional[dict], cover: Optional[bytes]) -> None:
    if not tags:
        return
    try:
        from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TRCK
        id3 = ID3()
        id3.add(TIT2(encoding=3, text=tags.get("title", "")))
        id3.add(TALB(encoding=3, text=tags.get("album", "")))
        id3.add(TPE1(encoding=3, text=tags.get("artist", "")))
        id3.add(TRCK(encoding=3, text=f"{tags.get('track', 1)}/{tags.get('total', 1)}"))
        if cover:
            id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="cover", data=cover))
        id3.save(path)
    except Exception:
        pass  # tagging is best-effort in the fake


def fake_build_m4b(chapters, out_path, *, tags: Optional[dict] = None,
                   cover: Optional[bytes] = None, bitrate: str = "96k") -> float:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    listfile = out_path.with_suffix(".concat.txt")
    listfile.write_text("".join(f"file '{p}'\n" for p, _title in chapters))
    try:
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
                       "-c:a", "aac", "-b:a", bitrate, str(out_path)], check=True, capture_output=True)
    finally:
        listfile.unlink(missing_ok=True)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(out_path)],
        check=True, capture_output=True, text=True,
    )
    return float(probe.stdout.strip())


def fake_footprint(pid: int) -> int:
    return 5_000_000_000


def fake_available_bytes() -> int:
    return 20_000_000_000


def fake_total_bytes() -> int:
    return 48_000_000_000
