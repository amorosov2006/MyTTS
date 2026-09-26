# MyTTS — project rules for agents

MyTTS is an offline book-to-audiobook converter: Qwen3-TTS 1.7B running on MLX on an Apple M5 Pro with 48 GB. It handles Russian and English books.

- `PLAN.md` holds the architecture and the phases.
- `mytts/contracts.py` holds the interfaces.
- `docs/API.md` holds the HTTP API.

## Hard rules

1. **Memory: everything launched must stay under 30 GB total.**
   - Check `vm_stat` before starting any model process.
   - Run real-model code only under `uv run python bench/memguard.py --limit-gb 14 -- <cmd>`.
   - Never run more than one real-model process at a time.
   - Never trust `mx.get_peak_memory()` as a safety signal.
2. **Only the lead runs real-model (GPU) code during parallel phases.** Implementer agents test with `mytts.tts.fake.FakeEngine` / `FakeVerifier`.
   - The exception is an agent explicitly assigned the engine. It runs `pytest -m gpu` under memguard and one process at a time.
3. **Do not edit `mytts/contracts.py`, `mytts/config.py` or `docs/API.md`.** Put any change you need in your final report instead.
4. **Offline at runtime.** No network calls in app code. Models load from `MYTTS_MODELS_DIR`.
   - That defaults to `<repo>/models`, which is gitignored.
   - **In a git worktree, export `MYTTS_MODELS_DIR=/Users/anatolius/Projects/MyTTS/models`.**
5. **Commands:**
   - Python 3.12 via uv: `uv sync`, `uv run pytest -q`. Default runs exclude the `gpu` marker.
   - Add deps with `uv add`, and list them in your report.
6. **Style:** match the surrounding code. Keep comment density low, use type hints, and keep functions short and pure where possible.
7. **Tests must pass before you report.** Put tests in `tests/test_<module>*.py` and use fixtures from `tests/fixtures/`.
8. **Final report:** files changed, deps added, test results (paste the summary line), known gaps, and any contract changes you need.
