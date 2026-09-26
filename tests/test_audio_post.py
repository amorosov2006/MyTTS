import json
import os
import subprocess

import numpy as np
import pytest
import soundfile as sf
from mutagen.id3 import ID3

from mytts.audio.post import SAMPLE_RATE, FFMPEG, assemble, build_m4b, process_segment, wav_duration

pytestmark = pytest.mark.skipif(not os.path.exists(FFMPEG), reason="ffmpeg not found")


def _tone(seconds, sr=SAMPLE_RATE, freq=440.0, amp=0.2, silence_s=0.2):
    n = int(seconds * sr)
    tone = amp * np.sin(2 * np.pi * freq * np.arange(n) / sr).astype(np.float32)
    pad = np.zeros(int(silence_s * sr), dtype=np.float32)
    return np.concatenate([pad, tone, pad])


def _write(path, audio, sr=SAMPLE_RATE):
    sf.write(str(path), audio, sr, subtype="PCM_16")
    return path


def _ffprobe(*args):
    out = subprocess.run(["ffprobe", "-v", "error", *args], capture_output=True, text=True, check=True)
    return out.stdout


# --------------------------------------------------------------------------------- process_segment

def test_process_segment_trims_silence(tmp_path):
    raw = _write(tmp_path / "raw.wav", _tone(1.0, silence_s=0.5))
    out = tmp_path / "out.wav"
    duration = process_segment(str(raw), str(out))
    assert out.exists()
    # trimmed duration should be close to the 1s tone + 2*40ms margins, well under the padded 2s
    assert 1.0 <= duration <= 1.2


def test_process_segment_speed_changes_duration(tmp_path):
    raw = _write(tmp_path / "raw.wav", _tone(2.0, silence_s=0.1))
    out_fast = tmp_path / "fast.wav"
    d_normal = process_segment(str(raw), str(tmp_path / "normal.wav"), speed=1.0)
    d_fast = process_segment(str(raw), str(out_fast), speed=2.0)
    assert d_fast == pytest.approx(d_normal / 2.0, rel=0.05)


def test_wav_duration(tmp_path):
    raw = _write(tmp_path / "raw.wav", _tone(1.5, silence_s=0.0))
    assert wav_duration(str(raw)) == pytest.approx(1.5, abs=0.01)


# --------------------------------------------------------------------------------- assemble

def test_assemble_duration_and_pauses(tmp_path):
    segs = []
    total_audio = 0.0
    for i in range(3):
        raw = _write(tmp_path / f"seg{i}.wav", _tone(0.5, silence_s=0.0))
        segs.append((str(raw), 300))
        total_audio += 0.5
    out = tmp_path / "chapter.mp3"
    duration = assemble(segs, str(out), tags={"title": "Ch1", "album": "Book", "artist": "Author",
                                              "track": "1/10"}, cover=None)
    # assemble() inserts pause_after_ms after every segment, including the last
    expected = total_audio + 3 * 0.3
    assert duration == pytest.approx(expected, abs=0.05)
    assert out.exists()

    id3 = ID3(str(out))
    assert str(id3["TIT2"]) == "Ch1"
    assert str(id3["TALB"]) == "Book"
    assert str(id3["TPE1"]) == "Author"
    assert str(id3["TRCK"]) == "1/10"
    assert str(id3["TCON"]) == "Audiobook"

    # encoded mp3 duration should roughly match too
    probed = float(_ffprobe("-show_entries", "format=duration", "-of",
                            "default=noprint_wrappers=1:nokey=1", str(out)).strip())
    assert probed == pytest.approx(expected, abs=0.3)


def test_assemble_loudness_normalized(tmp_path):
    # a loud tone should come out close to the target LUFS after normalization
    raw = _write(tmp_path / "loud.wav", _tone(3.0, amp=0.9, silence_s=0.1))
    out = tmp_path / "loud.mp3"
    assemble([(str(raw), 0)], str(out), tags={"title": "t"}, loudness_lufs=-18.0)

    measured = subprocess.run(
        ["ffmpeg", "-i", str(out), "-af", "loudnorm=I=-18:TP=-1.5:LRA=11:print_format=json",
         "-f", "null", "-"], capture_output=True, text=True)
    start, end = measured.stderr.rfind("{"), measured.stderr.rfind("}")
    stats = json.loads(measured.stderr[start:end + 1])
    assert abs(float(stats["input_i"]) - (-18.0)) <= 1.5


def test_assemble_requires_segments(tmp_path):
    with pytest.raises(ValueError):
        assemble([], str(tmp_path / "x.mp3"), tags={})


# --------------------------------------------------------------------------------- build_m4b

def test_build_m4b_chapters(tmp_path):
    chapters = []
    for i in range(3):
        raw = _write(tmp_path / f"ch{i}.wav", _tone(0.4, silence_s=0.0))
        chapters.append((str(raw), f"Chapter {i + 1}"))
    out = tmp_path / "book.m4b"
    duration = build_m4b(chapters, str(out), tags={"title": "Book", "artist": "Author"})
    assert out.exists()
    assert duration == pytest.approx(1.2, abs=0.05)

    probed = json.loads(_ffprobe("-print_format", "json", "-show_chapters", str(out)))
    assert len(probed["chapters"]) == 3
    assert probed["chapters"][0]["tags"]["title"] == "Chapter 1"
