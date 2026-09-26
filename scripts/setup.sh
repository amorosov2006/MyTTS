#!/usr/bin/env bash
# One-time ONLINE setup: system tools, Python env, models (~6 GB), frontend build.
# After this, MyTTS runs fully offline (scripts/run.sh).
set -euo pipefail
cd "$(dirname "$0")/.."

command -v brew >/dev/null || { echo "Homebrew is required: https://brew.sh"; exit 1; }
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v uv >/dev/null || brew install uv
command -v npm >/dev/null || brew install node

uv python install 3.12
uv sync

export HF_HOME="$PWD/models/hf" MYTTS_ONLINE=1
for repo in \
  mlx-community/Qwen3-TTS-12Hz-1.7B-Base-6bit \
  mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16 \
  mlx-community/whisper-large-v3-turbo; do
  echo "== $repo"; uv run hf download "$repo" --quiet >/dev/null
done

(cd frontend && npm install && npm run build)
echo "Setup complete. Start with: scripts/run.sh"
