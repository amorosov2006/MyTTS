#!/usr/bin/env bash
# Start MyTTS fully offline on http://127.0.0.1:8750 (opens the browser).
set -euo pipefail
cd "$(dirname "$0")/.."
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec uv run python -m mytts "$@"
