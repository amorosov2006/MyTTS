"""CPU-only audio post-processing: silence trim + tempo, chapter assembly (loudness-normalize,
encode, tag), and M4B build with chapter markers. Every function here is top-level and takes/
returns only picklable types, so they can run inside a ProcessPoolExecutor.

Requires ffmpeg (PLAN.md: /opt/homebrew/bin/ffmpeg) and mutagen.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TCON, TIT2, TPE1, TRCK

FFMPEG = os.environ.get("MYTTS_FFMPEG", "/opt/homebrew/bin/ffmpeg")

SAMPLE_RATE = 24000
SILENCE_DBFS = -45.0
TRIM_MARGIN_S = 0.04


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({cmd[0]}): {proc.stderr[-4000:]}")


def wav_duration(path: str) -> float:
    info = sf.info(path)
    return info.frames / info.samplerate


# --------------------------------------------------------------------------------- per-segment

def _trim_silence(audio: np.ndarray, sample_rate: int, threshold_dbfs: float = SILENCE_DBFS,
                   margin_s: float = TRIM_MARGIN_S) -> np.ndarray:
    if audio.size == 0:
        return audio
    mono = audio if audio.ndim == 1 else audio.mean(axis=1)
    threshold = 10 ** (threshold_dbfs / 20.0)
    loud = np.where(np.abs(mono) > threshold)[0]
    if loud.size == 0:
        return audio  # all silence: leave untouched rather than emit an empty file
    margin = int(margin_s * sample_rate)
    start = max(0, loud[0] - margin)
    end = min(len(mono), loud[-1] + 1 + margin)
    return audio[start:end]


def process_segment(raw_wav: str, out_wav: str, speed: float = 1.0) -> float:
    """Trim leading/trailing silence, apply tempo (ffmpeg atempo) only if speed != 1.0, write
    24 kHz mono PCM16 to out_wav. Returns the output duration in seconds."""
    audio, sr = sf.read(raw_wav, dtype="float32", always_2d=False)
    trimmed = _trim_silence(audio, sr)

    needs_ffmpeg = speed != 1.0 or sr != SAMPLE_RATE
    pre_path = out_wav + ".pre.wav" if needs_ffmpeg else out_wav
    sf.write(pre_path, trimmed, sr, subtype="PCM_16")

    if needs_ffmpeg:
        filters = [] if speed == 1.0 else [f"atempo={speed}"]
        cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", pre_path]
        if filters:
            cmd += ["-filter:a", ",".join(filters)]
        cmd += ["-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", out_wav]
        _run(cmd)
        os.remove(pre_path)

    return wav_duration(out_wav)


# --------------------------------------------------------------------------------- loudness

def _parse_loudnorm_json(stderr_text: str) -> Optional[dict]:
    start, end = stderr_text.rfind("{"), stderr_text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(stderr_text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _loudnorm(in_wav: str, out_wav: str, target_lufs: float) -> None:
    """Two-pass ffmpeg loudnorm with linear=true (a linear gain, not the dynamic single-pass
    filter) to avoid pumping."""
    measure = [FFMPEG, "-i", in_wav, "-af",
               f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"]
    proc = subprocess.run(measure, capture_output=True, text=True)
    stats = _parse_loudnorm_json(proc.stderr)
    if stats is None:
        _run([FFMPEG, "-y", "-loglevel", "error", "-i", in_wav, "-af",
              f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11", "-ar", str(SAMPLE_RATE), out_wav])
        return
    af = (f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:linear=true:"
          f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
          f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
          f"offset={stats['target_offset']}")
    _run([FFMPEG, "-y", "-loglevel", "error", "-i", in_wav, "-af", af, "-ar", str(SAMPLE_RATE), out_wav])


# --------------------------------------------------------------------------------- tagging

def _sniff_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"


def _write_id3(mp3_path: str, tags: dict, cover: Optional[bytes]) -> None:
    try:
        id3 = ID3(mp3_path)
    except ID3NoHeaderError:
        id3 = ID3()
    if tags.get("title"):
        id3.add(TIT2(encoding=3, text=tags["title"]))
    if tags.get("album"):
        id3.add(TALB(encoding=3, text=tags["album"]))
    if tags.get("artist"):
        id3.add(TPE1(encoding=3, text=tags["artist"]))
    if tags.get("track"):
        id3.add(TRCK(encoding=3, text=str(tags["track"])))
    id3.add(TCON(encoding=3, text="Audiobook"))
    if cover:
        id3.add(APIC(encoding=3, mime=_sniff_mime(cover), type=3, desc="Cover", data=cover))
    id3.save(mp3_path, v2_version=3)


# --------------------------------------------------------------------------------- assembly

def assemble(segments: list[tuple[str, int]], out_path: str, *, fmt: str = "mp3",
             bitrate: str = "96k", tags: Optional[dict] = None, cover: Optional[bytes] = None,
             loudness_lufs: float = -18.0) -> float:
    """Concatenate processed segment wavs -- each `(wav_path, pause_after_ms)` -- inserting
    `pause_after_ms` of silence between them, loudness-normalize to `loudness_lufs`, encode to
    mono `fmt` ("mp3" or "wav"), and (for mp3) write ID3 tags + cover. Atomic: `out_path` only
    appears once fully written. Returns total duration in seconds."""
    if not segments:
        raise ValueError("assemble() needs at least one segment")
    tags = tags or {}

    chunks = []
    for wav_path, pause_after_ms in segments:
        audio, sr = sf.read(wav_path, dtype="float32", always_2d=False)
        if sr != SAMPLE_RATE:
            raise ValueError(f"{wav_path}: expected {SAMPLE_RATE} Hz, got {sr}")
        chunks.append(audio)
        if pause_after_ms > 0:
            chunks.append(np.zeros(int(pause_after_ms * SAMPLE_RATE / 1000), dtype=np.float32))
    full = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    duration = len(full) / SAMPLE_RATE

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_path.parent) as tmpdir:
        concat_wav = os.path.join(tmpdir, "concat.wav")
        sf.write(concat_wav, full, SAMPLE_RATE, subtype="PCM_16")

        normalized_wav = os.path.join(tmpdir, "normalized.wav")
        _loudnorm(concat_wav, normalized_wav, loudness_lufs)

        tmp_out = os.path.join(tmpdir, f"out.{fmt}")
        if fmt == "mp3":
            _run([FFMPEG, "-y", "-loglevel", "error", "-i", normalized_wav,
                  "-ac", "1", "-c:a", "libmp3lame", "-b:a", bitrate, tmp_out])
            _write_id3(tmp_out, tags, cover)
        elif fmt == "wav":
            _run([FFMPEG, "-y", "-loglevel", "error", "-i", normalized_wav, "-ac", "1", tmp_out])
        else:
            raise ValueError(f"unsupported fmt {fmt!r}")

        os.replace(tmp_out, out_path)

    return duration


# --------------------------------------------------------------------------------- m4b

def _escape_ffmetadata(value: str) -> str:
    return (value.replace("\\", "\\\\").replace("=", "\\=")
            .replace(";", "\\;").replace("#", "\\#").replace("\n", "\\\n"))


def _write_ffmetadata(path: str, chapter_titles: list[str], durations: list[float],
                       tags: dict) -> None:
    lines = [";FFMETADATA1"]
    if tags.get("title"):
        lines.append(f"title={_escape_ffmetadata(tags['title'])}")
    if tags.get("artist"):
        lines.append(f"artist={_escape_ffmetadata(tags['artist'])}")
    if tags.get("album"):
        lines.append(f"album={_escape_ffmetadata(tags['album'])}")
    lines.append("genre=Audiobook")

    start_ms = 0
    for title, dur in zip(chapter_titles, durations):
        end_ms = start_ms + int(round(dur * 1000))
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start_ms}", f"END={end_ms}",
                  f"title={_escape_ffmetadata(title)}"]
        start_ms = end_ms

    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_m4b(chapters: list[tuple[str, str]], out_path: str, *, tags: Optional[dict] = None,
              cover: Optional[bytes] = None, bitrate: str = "64k") -> float:
    """chapters: `(wav_path, title)` in order. Concatenates them into one AAC .m4b with chapter
    markers (FFMETADATA1) and cover art. Returns total duration in seconds."""
    if not chapters:
        raise ValueError("build_m4b() needs at least one chapter")
    tags = tags or {}

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_path.parent) as tmpdir:
        durations = [wav_duration(wav_path) for wav_path, _ in chapters]

        concat_list = os.path.join(tmpdir, "concat.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            for wav_path, _title in chapters:
                f.write(f"file '{os.path.abspath(wav_path)}'\n")
        concat_wav = os.path.join(tmpdir, "concat.wav")
        _run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
              "-i", concat_list, "-c", "copy", concat_wav])

        metadata_path = os.path.join(tmpdir, "chapters.txt")
        _write_ffmetadata(metadata_path, [title for _, title in chapters], durations, tags)

        cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", concat_wav, "-i", metadata_path]
        maps = ["-map", "0:a"]
        if cover:
            cover_path = os.path.join(tmpdir, "cover.jpg")
            Path(cover_path).write_bytes(cover)
            cmd += ["-i", cover_path]
            maps += ["-map", "2:v"]
        cmd += maps + ["-map_metadata", "1", "-c:a", "aac", "-b:a", bitrate]
        if cover:
            cmd += ["-c:v", "copy", "-disposition:v", "attached_pic"]
        tmp_out = os.path.join(tmpdir, "out.m4b")
        cmd.append(tmp_out)
        _run(cmd)

        os.replace(tmp_out, out_path)

    return sum(durations)
