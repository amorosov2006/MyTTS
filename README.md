# MyTTS

MyTTS turns a book into an audiobook with one audio file per chapter. It runs entirely on your Mac and needs no internet at runtime.

- **Speech engine:** Qwen3-TTS 1.7B (open weights, Apache-2.0), running on the Apple GPU through MLX.
- **Languages:** Russian and English. Books that mix the two are handled paragraph by paragraph.
- **Interface:** a local web app. You pick a book, a voice and a style, listen to a **sample from the middle of the book**, then convert. You can play chapters while the rest of the book is still being generated.

## Optional: Google Gemini TTS (cloud)

Any book can be switched to Google's Gemini TTS, which gives very lifelike voices; its text is then sent to Google.

- Setup, pricing and privacy: [docs/GEMINI.md](docs/GEMINI.md).
- The local engine stays the default, and nothing leaves your Mac unless you choose Gemini for a book.

## Requirements

- An Apple Silicon Mac. It was developed on an M5 Pro with 48 GB. Machines with 16 GB or more should work, since the worker uses about 10 GB.
- Homebrew. The setup script installs the rest: `ffmpeg`, `uv` and Node.
- About 8 GB of disk for the models.

## Setup (once, needs internet)

```bash
scripts/setup.sh
```

This installs Python 3.12 and the dependencies, downloads the models (TTS, VoiceDesign and Whisper) into `models/`, and builds the web UI.

## Run (offline)

```bash
scripts/run.sh
```

This opens http://127.0.0.1:8750. The server only listens on localhost. While it runs, `caffeinate` keeps the Mac awake.

## How to use

1. **Book.** Drop in a file.
   - Supported formats: EPUB, FB2 / FB2.ZIP, DOCX, PDF (including scanned PDFs, which go through on-device OCR), TXT (any encoding), Markdown, HTML, RTF, ODT, DOC, MOBI and AZW3.
   - DRM-protected files cannot be read.
2. **Chapters.** Review the detected chapters. Front matter, the table of contents and notes are unticked by default. You can rename chapters and preview their text.
3. **Voice & style.** Choose a narrator: there are Russian male/female and English male/female voices. You can also create new ones:
   - **Design a voice** from a text description, for example "elderly storyteller, warm husky voice, slow pace".
   - **Clone a voice** from a 5–30 s recording plus its exact transcript. Only use voices you have the right to use.
   - Other settings: speed, expressiveness, pauses, reading chapter titles, skipping footnotes, the Whisper quality check, output format (MP3 per chapter / M4B audiobook / both) and output folder.
4. **Sample.** Render about 60 s from the middle of the book, or from any chapter and position, or from your own text. It uses exactly the book's settings. Listen, adjust, re-render, then approve.
5. **Convert.** Start, pause, resume or cancel. Chapters appear as they finish, and the player can follow the chapter currently being generated.
   - A finished book can be **converted again** with other settings. Each conversion gets its own folder.
   - Jobs survive app restarts: resume continues where it stopped.

The output goes to `~/Audiobooks/<Author - Title>/NN - Chapter.mp3` (and `<Author - Title>.m4b` if you chose M4B). The files are tagged and include the cover.

## Performance (M5 Pro, measured)

| Setting | Speed |
|---|---|
| Quality check on (default) | ~5× real time: a 10-hour book takes about 2 hours |
| Quality check off | ~8.5× real time |

- **Worker memory:** about 6–10 GB. The models are released after 10 minutes idle.
- **Why only one GPU worker:** several processes on one Apple GPU don't add throughput; three processes collapsed to 3.5× in total. Instead, MyTTS runs one worker that synthesizes 8 segments per GPU pass, plus a pool of CPU processes for audio post-processing.

## How it works

```
book file ─▶ ingest (per-format parser) ─▶ chapters/paragraphs
          ─▶ text (normalize numbers/abbreviations/ё, split into 1–3 sentence segments)
          ─▶ scheduler ─▶ GPU worker subprocess: Qwen3-TTS batch=8 (+ Whisper QA, retries)
          ─▶ CPU pool: trim, tempo, loudness (−18 LUFS), MP3/M4B + tags
          ─▶ FastAPI + Server-Sent Events ─▶ web UI / player
```

**Quality check.** Whisper transcribes every segment back and compares it with the text. Segments with skipped or garbled words, or with runaway generation, are regenerated automatically.

**Memory safety.** The GPU worker runs in a supervised subprocess. The supervisor reads the process's real memory footprint (the same number Activity Monitor shows) and kills and restarts the worker above 18 GB. It also refuses to start the worker when the Mac lacks free memory. Each segment has a generation-length cap, so a model that "never stops talking" can't balloon.

## Development

```bash
uv run pytest -q
```

This runs about 300 fast tests with a fake engine and needs no GPU.

```bash
uv run python bench/memguard.py --limit-gb 16 -- uv run pytest -m gpu -q
```

This runs the real-model tests under a memory guard.

```bash
uv run python -m mytts --fake
```

This starts the full app with a fake engine, which is useful for UI work.

For the UI dev server with a mock backend, run `cd frontend && npm run dev:all`.

The rest of the project documentation:
- `PLAN.md`: architecture, decisions and benchmark results.
- `docs/API.md`: the HTTP API.
- `mytts/contracts.py`: the interfaces between modules.
- `bench/`: the Phase 0 benchmarks and the listening test.

## Known limitations

- Russian word stress comes from the model. Qwen3-TTS doesn't accept stress marks, so homographs such as «за́мок / замо́к» can be misread.
- Russian number agreement is heuristic. Case after most prepositions and grammatical gender for 1/2 are handled; rarer constructions may read in the nominative case.
- Complex PDF layouts (multi-column pages, heavy footnotes) are parsed heuristically. Check the chapter preview before converting.
