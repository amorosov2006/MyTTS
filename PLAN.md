# MyTTS — Offline Book → Audiobook Converter

Status: **v0.1 complete (2026-09-25)** — all phases done; see §10 for final verification.

## 1. Target machine (measured 2026-09-25)

| Item | Value |
|---|---|
| CPU/GPU | Apple M5 Pro — 15 CPU cores (5 perf + 10 eff), 16-core GPU |
| Memory | 48 GB unified |
| OS | macOS 27.0 |
| Disk free | ~812 GB |
| Tooling present | uv, brew, git, node/npm (mise), Python 3.13 |
| Tooling missing | **ffmpeg** (needed), Python 3.12 (uv will install it) |

## 2. Engine choice

**Primary: Qwen3-TTS (12Hz, Apache-2.0) via `mlx-audio` on the Apple GPU (MLX).**

Why:
- Open weights, Apache-2.0 license, supports both Russian and English.
- Native Apple-Silicon runtime (MLX) with quantized weights (bf16/8-bit/4-bit) and **in-process batch generation**.
  mlx-audio reports ~1.7× throughput at batch 2 and ~5.5× at batch 8. These numbers come from unspecified hardware and have not been measured on this Mac yet.
- Three model types:
  - **CustomVoice:** 9 preset speakers, plus spoken instructions for tone and emotion.
  - **VoiceDesign:** builds a voice from a text description.
  - **Base:** clones a voice from a 10–15 s reference clip.

Known weaknesses the design must handle:
1. **Long-input drift.** On inputs over about 1500 words the model skips or invents text, and its speaking rate creeps up.
   → Feed it short segments (1–3 sentences, ≤ ~400 chars). Never feed a whole chapter.
2. **None of the 9 preset speakers is a native Russian speaker.** The presets are Chinese, English, Japanese and Korean.
   → For Russian, use VoiceDesign to create a narrator once, save that clip as a reference, then clone it with Base for the whole book. This keeps the voice consistent across chapters.
3. **Russian stress (ударения) and homographs** (e.g. замок) are unreliable, and the model ignores stress marks.
   → Text normalization helps: numbers → words, abbreviations, ё-restoration. Stress remains a residual risk.
4. **The official `qwen-tts` package targets PyTorch/CUDA.** Mac support comes through mlx-audio.
   mlx-audio's TTS extras currently fail to install on Python 3.13 → **pin Python 3.12**.

**Optional secondary engine (pluggable): Silero v5 for Russian.**
- It predicts stress and handles homographs automatically, and it is very fast on CPU.
- Its voice quality is less natural than Qwen3's.
- Its model licensing has tiers: verify before any commercial use.

The engine interface is abstract, so adding a secondary engine later is cheap.

Rejected alternatives:

| Engine | Reason |
|---|---|
| Kokoro | No official Russian |
| XTTS-v2 | Non-commercial CPML license, abandoned project |
| Fish/OpenAudio S1 | CC-BY-NC license |
| Piper | Russian quality is noticeably lower |
| Chatterbox-multilingual | MIT license, but watermarks its audio; a possible fallback |
| CosyVoice 3 | Viable; kept as a backup |

## 3. Parallelism — honest assessment

- Memory is **not** the bottleneck. The 1.7B model is about 4 GB in bf16, so 48 GB fits several copies.
- The **GPU is the bottleneck.** Several processes on one Apple GPU mostly queue up behind each other on Metal, so throughput barely rises.
- The gain comes from **batching inside one process**: 4–8 segments per forward pass.
- The plan:
  - **1 GPU worker process** does batched TTS.
  - **A CPU process pool** handles parsing, normalization, trimming, loudness normalization and MP3/M4B encoding in parallel.
  - The CPU pool also handles the optional Russian Silero engine, which is CPU-only and therefore runs truly in parallel.
- **Phase 0 benchmark** decides the defaults empirically. Matrix:
  - model 0.6B vs 1.7B
  - bf16 vs 8-bit
  - batch 1 / 4 / 8
  - 1 vs 2 GPU worker processes

  Output: real-time factor (RTF) and a book-level ETA.
- Worker count and batch size stay user-configurable in the UI's Advanced panel.

### Phase 0 results (measured 2026-09-25, M5 Pro)

Unless noted, the runs cloned the Russian male voice with 32 segments.

| Config | Speed (× real-time) | Real peak memory |
|---|---|---|
| 1.7B bf16, batch 1 | 2.5× | 11 GB |
| 1.7B bf16, batch 8 | 7.4× | 11.2 GB |
| **1.7B 6-bit, batch 8** ★ | **8.6× RU / 8.9× EN** | **9.6 GB** |
| 1.7B 6-bit, batch 16 | 5.9× (padding waste) | — |
| 0.6B bf16, batch 8 | 6.7× (earlier run, before the token cap) | — |
| 2 processes × (6-bit, batch 8) | 9.2× total (+7%) | 20.3 GB |
| 3 processes (before the token cap) | 3.5× total — **collapse** | unsafe |

Findings:
- **Default: 1 GPU worker, Qwen3-TTS 1.7B 6-bit, batch 8.**
  - A second worker adds only +7% speed for 2× the memory. It is not offered by default.
  - A 10-hour book takes about 70 min of generation, before the QA pass.
- **Quality (Whisper CER on 8 segments):**
  - 6-bit is the same as bf16, and batch 8 is the same as batch 1.
  - Russian: ~0% on normal prose. English: ~1%.
  - The only miss was a false alarm: Whisper writes digits while the source spells the number in words. **The QA scorer must normalize numbers on both sides.**
- **Whisper QA** (large-v3-turbo) peaks at 3 GB and takes ~0.08 s per second of audio.
- **Silero v5.5** runs ~100× real-time on CPU only (MPS is unsupported). Its license is **CC BY-NC-SA 4.0**.
  - Without normalization it silently drops numbers: "В 1891 г. … 3 дома …" loses all the digits.

### Memory safety (hard rule, after an incident on 2026-09-25)

Two unguarded MLX processes reached ~36 GB each. MLX's own `peak_memory` counter under-reported this badly.

Rules:
- **Total footprint of everything we launch must stay < 30 GB.**
- The GPU worker calls `mx.set_memory_limit(12 GB)` and `mx.set_cache_limit(2 GB)`, and calls `mx.clear_cache()` after each batch.
- **Per-segment `max_tokens` = 1.5 × chars + 60.** A sequence that never emits end-of-speech would otherwise run the whole batch to 4096 tokens, bloating memory. It is also slower: the cap raised speed from 6.4× to 8.6×. If a segment hits the cap, it is flagged for regeneration.
- The app's worker supervisor uses the same logic as `bench/memguard.py`:
  - it reads real `phys_footprint` via libproc and available memory via vm_stat;
  - it refuses to start without headroom;
  - it kills and restarts the worker above the cap.
- Budget: TTS worker ~10 GB + Whisper ~3 GB + app/UI ~1 GB ≈ **14 GB**.

## 4. Architecture

```
MyTTS/
  backend/                 Python 3.12 (uv), FastAPI + uvicorn, bound to 127.0.0.1 only
    app/contracts.py       Pydantic models + Engine Protocol  (owned by lead; frozen after Phase 1)
    app/api.py             REST + Server-Sent Events (progress, segment-ready, chapter-ready)
    ingest/                one parser per format → Book{title, author, lang, cover, chapters[paragraphs]}
    text/                  normalize_ru, normalize_en, language detect, segmenter
    tts/                   engine_qwen_mlx.py, engine_fake.py (tests), [engine_silero.py]
    pipeline/              job store (SQLite, resumable), GPU scheduler, CPU encode pool, QA (ASR check)
  frontend/                Svelte + Vite + Tailwind → built to backend/static (no Node needed at runtime)
  models/                  model weights (downloaded once)
  scripts/setup.sh         one-time online install (deps, ffmpeg, models)
  scripts/run.sh           offline start: HF_HUB_OFFLINE=1, caffeinate, open browser
  tests/fixtures/          small RU + EN books in every input format
```

### Input formats

| Format | Library | Notes |
|---|---|---|
| epub | ebooklib + BeautifulSoup | Chapters from the table of contents/spine; cover extracted |
| fb2 / fb2.zip | lxml | `<section>` → chapter; cover |
| docx | python-docx | Heading styles → chapters |
| pdf | PyMuPDF | Strip headers, footers and page numbers; re-join hyphenated words; outline → chapters. Scanned PDFs need OCR (Apple Vision via `ocrmac`, offline) |
| txt | built-in | Detect headings ("Глава N", "Chapter N", "Часть", Roman numerals) and the text encoding (cp1251/utf-8) |
| md | markdown-it | `#`/`##` → chapters |
| **added:** rtf, odt, doc, html | macOS built-in `textutil` → html → same path | Free, offline |
| **added:** mobi/azw3 | `mobi` package | DRM-free files only |

DRM-protected books cannot be read, whatever the format.

### Processing flow

1. **Upload and parse.** Upload the file, parse it, then show a **chapter review list** in the UI: include/exclude, rename, merge, preview text.
2. **Prepare text.** Normalize the text, then segment it into chunks of ≤ ~400 chars at sentence boundaries.
3. **Generate speech.** The GPU scheduler processes segments **in chapter order**, so chapter 1 finishes first. It batches N segments per call.
4. **Assemble audio (CPU pool).**
   - Trim leading/trailing silence and add consistent pauses (sentence / paragraph / chapter).
   - Normalize loudness to −18 LUFS.
   - Concatenate and encode into chapter files, with ID3 tags and the cover image.
5. **Stream to the page.** Server-Sent Events push progress and ETA. The player can play:
   - finished chapters, and
   - the chapter still being generated, segment by segment, as each one finishes.
6. **Optional QA pass.** mlx-whisper transcribes each segment offline and compares it to the source text. Segments with a high error rate are regenerated, up to 2 retries. This catches the skip/hallucination failure mode.
7. **Resume.** Jobs are persisted, so stop/pause/resume works across restarts.

### Front end

- A single dynamic page with:
  - drag-and-drop file picker
  - language auto-detect with override
  - voice gallery with a "▶ preview" button that speaks a sample sentence
  - style instruction and speed controls
  - output-format options
- **Output folder:** a browser can't hand a real disk path to a server, so a "Choose folder…" button opens the **native macOS folder dialog** through the local backend (`osascript`).
- **Player:** chapter playlist, progress bars per chapter, 0.75–2× speed, skip ±15 s, and a remembered position.

## 5. Agent orchestration

| Role | Model | Responsibilities |
|---|---|---|
| **Lead** (this session) | Opus | Architecture; owns `contracts.py` and the API spec; splits the work; reviews every merge; integration; hard debugging; final end-to-end runs |
| **Implementers** | Sonnet | One module each, in an isolated git worktree. They get: the contracts, the module's acceptance tests, and a "don't touch" list |
| **Utility agents** | Haiku | Generate test fixtures (same short RU/EN text in every format); run test/lint suites; triage logs; write docs |
| **Reviewer** | Opus | `/code-review` at the end of each phase, focused on the scheduler, streaming and normalization |

Rules for implementer agents:
1. Do not change `contracts.py`. Request changes from the lead instead.
2. Every module ships with tests that pass under `uv run pytest`, using `FakeEngine` so no GPU is needed.
3. The final report lists the files changed, the test results, and any known gaps.
4. Nothing is merged until the lead has reviewed it.

### Phases

| Phase | Who | Work | Gate |
|---|---|---|---|
| **0. Spike & benchmark** | Lead + 1 Sonnet | `git init`; install Python 3.12 venv, ffmpeg, mlx-audio; download models; benchmark matrix (§3); render RU + EN sample clips with 4–6 candidate voices | **User listens to the samples and picks the model and voices** |
| **1. Skeleton & contracts** | Lead + 1 Haiku (fixtures) | Repo layout, contracts, FakeEngine, pytest harness, fixtures in all formats | Harness green |
| **2. Parallel build** | 4 Sonnet agents, one worktree each | **A** ingest parsers · **B** text normalization + segmentation (RU/EN) · **C** TTS engine + GPU scheduler + audio post-processing + job store · **D** front end against a mocked API | Each module's tests green; lead review |
| **3. Integration** | Lead (+ Haiku test runners) | Wire A–D; end-to-end test in every format; one real full-length book; test the browser UI with the built-in browser pane | E2E green; Opus code review |
| **4. Tuning & polish** | Lead + 1 Sonnet | Apply benchmark defaults; QA pass; UX polish; `setup.sh`/`run.sh`; README | User acceptance |

Estimated agent count: 1 lead + at most 6 sub-agents, with no more than 4 running at once.

## 6. Test strategy

- **Unit tests:** parsers against fixtures; golden tests for normalization (RU numbers, dates, abbreviations, ё); segmenter limits.
- **Pipeline tests:** use FakeEngine (tones/silence of the proper length), so ordering, resume, cancel and SSE events are tested in seconds.
- **Real-engine smoke test:** synthesize 3 segments; mlx-whisper transcribes them back; assert character error rate below a threshold.
- **UI tests:** built-in browser pane with screenshots and simulated clicks; check that the player starts while the job is still running.

## 7. Rough expectations (to be replaced by Phase 0 numbers)

- A 100k-word novel is about **10–11 hours of audio**.
- Total wall time is RTF × 10.5 h. RTF (real-time factor) = generation time ÷ audio length, so RTF 0.3 means 1 h of audio takes 18 min.
- Example: at RTF 0.3 the novel takes ~3 h. The real RTF on this Mac is **unknown until measured**.

## 8. Decisions (confirmed by user 2026-09-25 — "go with defaults")

1. **Model:** Qwen3-TTS 1.7B, with the final variant (bf16/8-bit/6-bit) chosen by the Phase 0 benchmark.
2. **Russian voice:** designed once with VoiceDesign, then cloned with Base for the whole book, so the voice stays consistent.
3. **Silero:** not an engine yet. The engine interface stays pluggable, and a Silero sample is included in the Phase 0 listening test.
4. **Output:** one MP3 per chapter (mono, 96 kbps) is the default. A single M4B with chapter markers is optional.
5. **Whisper QA:** on — mlx-whisper transcribes each segment back, and failing segments are regenerated.
6. **Footnotes:** skipped by default.
7. **Scanned-PDF OCR:** included, using Apple Vision via `ocrmac`.
8. **Use:** personal, non-commercial.
9. **Phase 0:** approved to run, including internet downloads.

## 9. Added requirement: sample preview before the full conversion

Before starting a full conversion, the user can **render a short sample from the middle of the book** and listen to it:
- Default: about 60 s (roughly 3–6 segments) taken from the middle chapter.
- The user can also choose a chapter and position, or paste their own text.

The sample runs through the **exact same pipeline and settings** as the full job: normalization, voice, style, speed, QA, post-processing and encoding. What the user hears is therefore what the book will sound like.

The UI flow is **Parse → Review chapters → Choose voice → ▶ Render sample → (adjust & re-sample) → Convert all**. The "Convert all" button is highlighted once a sample has been approved for the current settings. If the user changes any setting after approving, the page reminds them to re-sample.

- **API:** `POST /jobs/{id}/sample {chapter?, offset?, seconds?, text?}` returns a sample id.
- **Delivery:** the audio is streamed over SSE and played in the same player.
- **Storage:** samples are kept under `<output>/.samples/`, so the user can A/B-compare them.

## 10. Final verification (2026-09-25)

### Tests
- **Default suite:** 358 tests, fake engine, no GPU. It passed 6 consecutive runs.
- **Real-model suite:** 4 tests, run under memguard with a peak of 12.7 GB.

### Real end-to-end runs (M5 Pro)

| Book | Result |
|---|---|
| RU epub | 3 chapters, CER 0% on prose |
| EN docx → MP3 + M4B | Notes excluded automatically; M4B chapter markers correct; CER 0% |
| RU fb2, 21.6k chars | 24.5 min of audio in 4 min 50 s (**5.2× real time with QA**) |
| RU scanned PDF | Converted through on-device OCR |

In the 21.6k-char run the worker used 5.9–9.9 GB, and the session-wide peak across all processes was 14.3 GB.

### Features verified in the browser
- sample from the middle of the book, with progressive playback
- approve the sample, then convert
- play a chapter while it is still being generated, with automatic switch to the final MP3 and auto-advance
- pause/resume
- hard-kill the server, restart and resume without redoing finished work
- design a voice (VoiceDesign model swap, ~7 s)
- clone a voice from an m4a clip
- Convert again
- responsive layout at 375–1440 px

### Normalization (heard via Whisper)
Years, dates, money, house numbers, time, centuries, percentages, "т.е.", 1/2 gender agreement, case after prepositions, and ё in common words and names.

### Code review (Opus)
- The review raised 14 findings, and all are fixed with regression tests.
- Security: SPA path traversal, DNS-rebinding/CSRF (TrustedHost and Origin checks), AppleScript injection, zip bombs, ffmpeg protocol whitelist, upload caps.
- Robustness: stuck "assembling" chapters, stalled samples, pause/resume double synthesis, the double-start race, orphaned processes after SIGKILL (child watchdog thread plus pool initializer), a fail-safe memory supervisor, blocking I/O on the event loop, and bounded shutdown.

### Known limitations
- Russian stress on homographs depends on the model.
- Rare case-agreement constructions may be read in the nominative.
- Complex PDF layouts are parsed heuristically.
- MOBI support is untested because there is no fixture.
