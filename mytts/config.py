"""Paths, limits and defaults. Everything is overridable via MYTTS_* environment variables."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _path(env: str, default: Path) -> Path:
    return Path(os.environ.get(env, default)).expanduser().resolve()


# Worktrees don't contain models/ (gitignored): point MYTTS_MODELS_DIR at the main checkout.
MODELS_DIR = _path("MYTTS_MODELS_DIR", REPO_ROOT / "models")
VOICES_DIR = _path("MYTTS_VOICES_DIR", REPO_ROOT / "voices")          # built-in voices (in git)
USER_VOICES_DIR = _path("MYTTS_USER_VOICES_DIR", Path("~/Library/Application Support/MyTTS/voices"))
DATA_DIR = _path("MYTTS_DATA_DIR", Path("~/Library/Application Support/MyTTS"))  # SQLite, uploads
DEFAULT_OUTPUT_DIR = _path("MYTTS_OUTPUT_DIR", Path("~/Audiobooks"))
STATIC_DIR = REPO_ROOT / "mytts" / "static"                              # built frontend

os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf"))
if os.environ.get("MYTTS_ONLINE") != "1":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

HOST = "127.0.0.1"
PORT = int(os.environ.get("MYTTS_PORT", "8750"))

# --- Models
TTS_MODEL = os.environ.get("MYTTS_TTS_MODEL", "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-6bit")
VOICE_DESIGN_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16"
ASR_MODEL = "mlx-community/whisper-large-v3-turbo"
SAMPLE_RATE = 24000

# --- Throughput (Phase 0: batch 8 on one worker is the sweet spot)
BATCH_SIZE = int(os.environ.get("MYTTS_BATCH_SIZE", "8"))
SEGMENT_MAX_CHARS = 350
SEGMENT_TARGET_CHARS = 220

# --- Memory safety (hard rule: everything we launch < 30 GB total)
WORKER_MEM_CAP_GB = float(os.environ.get("MYTTS_WORKER_MEM_CAP_GB", "18"))  # kill+restart above
MIN_SYSTEM_FREE_GB = 6.0                  # refuse to (re)start worker below this
MLX_MEMORY_LIMIT_GB = 12
MLX_CACHE_LIMIT_GB = 2
WORKER_IDLE_UNLOAD_S = float(os.environ.get("MYTTS_WORKER_IDLE_UNLOAD_S", "600"))  # free models when idle


def token_budget(text: str) -> int:
    """max_tokens for one segment. Codec = 12.5 tokens/s of audio; speech ~12+ chars/s."""
    return int(len(text) * 1.5) + 60


# --- QA
QA_MAX_CER = 0.12        # above this (after number normalization) a segment is regenerated
QA_MAX_ATTEMPTS = 3

# --- Audio output defaults
MP3_BITRATE = "96k"
LOUDNESS_LUFS = -18.0
PAUSE_SENTENCE_MS = 250   # between segments inside a paragraph
PAUSE_PARAGRAPH_MS = 700
PAUSE_CHAPTER_TITLE_MS = 1200


# --- Google Gemini TTS (optional cloud engine; off unless a job selects it)
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODEL = os.environ.get("MYTTS_GEMINI_MODEL", "gemini-3.8-flash-tts")
GEMINI_MODELS = {  # id: (label, USD per 1M output audio tokens through 2026-12-31)
    "gemini-3.8-flash-tts": ("Gemini 3.8 Flash TTS — best quality", 9.00),
    "gemini-3.8-flash-lite-tts": ("Gemini 3.8 Flash-Lite TTS — cheaper, faster", 6.00),
}
GEMINI_INPUT_USD_PER_M = 0.50
GEMINI_AUDIO_TOKENS_PER_S = 25
GEMINI_CONCURRENCY = int(os.environ.get("MYTTS_GEMINI_CONCURRENCY", "4"))
GEMINI_START_RPM = float(os.environ.get("MYTTS_GEMINI_RPM", "10"))  # adapts to the real quota
GEMINI_MIN_RPM = 1.0
GEMINI_MAX_RPM = 120.0
GEMINI_TIMEOUT_S = 180
GEMINI_MAX_RETRIES = 8
# Larger segments than the local model: a paragraph reads with better flow and costs fewer requests
GEMINI_SEGMENT_TARGET_CHARS = 700
GEMINI_SEGMENT_MAX_CHARS = 1400
GEMINI_DEFAULT_STYLE = {
    "ru": "Спокойное, тёплое, выразительное чтение аудиокниги профессиональным диктором.",
    "en": "Calm, warm, expressive audiobook narration by a professional narrator.",
}
GEMINI_DEFAULT_VOICE = {"ru": "Charon", "en": "Charon"}
