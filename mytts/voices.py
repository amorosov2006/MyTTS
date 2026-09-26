"""Voice registry: built-in voices (config.VOICES_DIR) + user voices (config.USER_VOICES_DIR).

Layout for both roots: ``<root>/<voice_id>/voice.json`` + ``<root>/<voice_id>/ref.wav``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from mytts import config
from mytts.contracts import Lang, TTSWorker, Voice

MIN_CLONE_S = 5.0
MAX_CLONE_S = 30.0

# Reference sentences for voice design, in the style of the built-in voices' ref_text.
_REF_SENTENCES = {
    Lang.ru: "Здравствуйте. Меня зовут Алексей, и сегодня я прочитаю для вас эту книгу."
             " Устраивайтесь поудобнее, мы начинаем.",
    Lang.en: "Hello. My name is Alex, and today I will be reading this book for you."
             " Make yourself comfortable, and let us begin.",
}


class VoiceError(Exception):
    """Bad input for voice cloning/design (shown to the user, 4xx)."""


def reference_sentence(lang: Lang) -> str:
    return _REF_SENTENCES.get(lang, _REF_SENTENCES[Lang.en])


class VoiceRegistry:
    def __init__(self, builtin_dir: Optional[Path] = None, user_dir: Optional[Path] = None):
        self.builtin_dir = Path(builtin_dir) if builtin_dir is not None else config.VOICES_DIR
        self.user_dir = Path(user_dir) if user_dir is not None else config.USER_VOICES_DIR
        self.user_dir.mkdir(parents=True, exist_ok=True)

    def _load_dir(self, root: Path, builtin: bool) -> list[Voice]:
        voices = []
        if not root.is_dir():
            return voices
        for d in sorted(root.iterdir()):
            meta_path = d / "voice.json"
            if not d.is_dir() or not meta_path.is_file():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            ref = d / "ref.wav"
            voices.append(Voice(
                id=meta["id"],
                name=meta["name"],
                lang=Lang(meta["lang"]),
                gender=meta.get("gender"),
                ref_audio=str(ref.resolve()),
                ref_text=meta.get("ref_text", ""),
                description=meta.get("description", ""),
                builtin=builtin,
            ))
        return voices

    def list(self) -> list[Voice]:
        return self._load_dir(self.builtin_dir, True) + self._load_dir(self.user_dir, False)

    def get(self, voice_id: str) -> Optional[Voice]:
        for v in self.list():
            if v.id == voice_id:
                return v
        return None

    def _new_voice_dir(self, prefix: str, voice_id: Optional[str] = None) -> tuple[str, Path]:
        voice_id = voice_id or f"{prefix}_{uuid.uuid4().hex[:8]}"
        d = self.user_dir / voice_id
        d.mkdir(parents=True, exist_ok=True)
        return voice_id, d

    def add_cloned(self, audio_path: Path, transcript: str, name: str, lang: Lang,
                    gender: Optional[str] = None) -> Voice:
        """Convert `audio_path` to a 24 kHz mono wav and register it as a user voice.
        Raises VoiceError if the clip isn't 5-30s long."""
        voice_id, d = self._new_voice_dir("clone")
        try:
            out_wav = d / "ref.wav"
            duration = _to_wav(Path(audio_path), out_wav)
            if not (MIN_CLONE_S <= duration <= MAX_CLONE_S):
                raise VoiceError(
                    f"reference audio must be {MIN_CLONE_S:.0f}-{MAX_CLONE_S:.0f}s long, got {duration:.1f}s"
                )
            _write_meta(d, voice_id, name, lang, gender, transcript, "")
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
        return self.get(voice_id)

    async def add_designed(self, worker: TTSWorker, description: str, lang: Lang, name: str,
                            gender: Optional[str] = None, voice_id: Optional[str] = None) -> Voice:
        """Render a reference sentence with the VoiceDesign model and register it."""
        voice_id, d = self._new_voice_dir("design", voice_id)
        try:
            out_wav = d / "ref.wav"
            ref_text = reference_sentence(lang)
            await worker.design_voice(description, lang, ref_text, str(out_wav))
            _write_meta(d, voice_id, name, lang, gender, ref_text, description)
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
        return self.get(voice_id)

    def delete(self, voice_id: str) -> None:
        v = self.get(voice_id)
        if v is None:
            raise KeyError(voice_id)
        if v.builtin:
            raise PermissionError("built-in voices cannot be deleted")
        shutil.rmtree(self.user_dir / voice_id, ignore_errors=True)


def _write_meta(d: Path, voice_id: str, name: str, lang: Lang, gender: Optional[str],
                ref_text: str, description: str) -> None:
    meta = {
        "id": voice_id, "name": name, "lang": lang.value, "gender": gender,
        "ref_text": ref_text, "description": description, "builtin": False,
    }
    (d / "voice.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _to_wav(src: Path, dst: Path) -> float:
    """ffmpeg -> 24 kHz mono wav at `dst`. Returns duration in seconds.

    `-protocol_whitelist file,pipe` keeps ffmpeg from opening anything but plain
    files (no `concat:`/network protocols) when fed an untrusted upload."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-protocol_whitelist", "file,pipe", "-y", "-i", str(src),
         "-ac", "1", "-ar", str(config.SAMPLE_RATE), str(dst)],
        check=True, capture_output=True, timeout=60,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries",
         "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(dst)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return float(probe.stdout.strip())
