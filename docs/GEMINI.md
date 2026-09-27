# Using Google Gemini TTS in MyTTS

MyTTS normally runs fully offline with Qwen3-TTS on your Mac. You can switch **individual books** to Google's Gemini TTS, which sounds more lifelike. When you do, the book text is sent to Google.

The facts below were checked on 2026-09-27 at ai.google.dev. Prices and plans change, so re-check the links.

## 1. What your Google AI subscription does (and doesn't) cover

- **The consumer plan (Google AI Plus / Pro) does not include Gemini API usage.** Google's own wording: plan benefits "apply only within the Google AI Studio web interface. Direct use of the Gemini API … is billed and managed separately." ([ai.google.dev/gemini-api/docs/google-ai-plans](https://ai.google.dev/gemini-api/docs/google-ai-plans))
- **Possible perk on Google AI Pro:** it has included *Google Developer Program Premium*, with a monthly Google Cloud credit (about $10) that can offset pay-as-you-go API spend. Check whether your account has it at [developers.google.com/program](https://developers.google.com/program/plans-and-pricing).

## 2. Which model to use

These are Gemini API prices per 1M tokens, from [the pricing page](https://ai.google.dev/gemini-api/docs/pricing). Audio is billed at 25 tokens per second of speech.

| Model (MyTTS option) | Audio output price | ≈ cost of a 10-hour book |
|---|---|---|
| `gemini-3.8-flash-tts` (**default**, best) | $9 (→ $18 from 2027-01-01) | ≈ $8 |
| `gemini-3.8-flash-lite-tts` (cheaper, faster) | $6 (→ $12 from 2027) | ≈ $5.40 |

MyTTS offers only the 3.8 models. They have no free tier, so billing must be enabled on the key's project. Because the project is paid, Google doesn't use what you send to improve its products; see the [Gemini API terms](https://ai.google.dev/gemini-api/terms).

## 3. Create the API key (about 3 minutes)

1. **Create the key.**
   - Open **https://aistudio.google.com/apikey** and sign in with your Google account.
   - Accept the terms if asked.
   - Click **Create API key**.
   - Choose **Create API key in new project**, or pick an existing Google Cloud project.
   - Copy the key; it starts with `AIza…`. Treat it like a password.
2. **Enable billing** (required for the 3.8 models).
   - In AI Studio, next to your key/project, click **Set up billing**. You can also go to [console.cloud.google.com/billing](https://console.cloud.google.com/billing) and link a billing account to the project.
   - Optional but recommended: in the Cloud console go to **Billing → Budgets & alerts** and set a monthly budget (e.g. $20) with email alerts.
3. **Check your rate limits** at [aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit). They depend on your tier.
   - MyTTS sends 4 requests at a time and waits automatically when Google says "slow down".
   - To use fewer parallel requests, start MyTTS with `MYTTS_GEMINI_CONCURRENCY=2 scripts/run.sh`.

## 4. Connect it in MyTTS

- **Option A (recommended): in the app.**
  - Click ⚙ (top bar) → **Cloud engines** → paste the key → **Save & verify**. Or do it from a book's **Voice & style** step.
  - MyTTS checks the key with Google first, then stores it in `~/Library/Application Support/MyTTS/secrets.json`.
  - That file is readable only by your macOS user and is never committed to git.
  - The app shows only the last 4 characters of the key after that.
- **Option B: an environment variable.** Start the app with `GEMINI_API_KEY=AIza… scripts/run.sh`. The variable takes precedence over the saved key.

To remove the key, click ⚙ → **Remove key**, or delete `secrets.json`.

## 5. Use it for a book

1. Upload the book as usual and review the chapters.
2. On **Voice & style**, switch the engine to **Google Gemini (cloud)**.
3. Pick a **model** and a **voice**. There are 30 voices; press ▶ to hear a short preview, which costs one tiny request and is cached.
4. Optionally describe the **style**, e.g. "Спокойное тёплое чтение аудиокниги" or "lively, with distinct character voices".
5. The screen shows an estimated cost for the whole book.
6. **Sample**: render a sample from the middle of the book and listen. This is strongly recommended before a whole-book run.
7. **Convert.** Everything else works as with the local engine, including playing chapters while the rest is being generated, pause/resume, MP3/M4B output, and Convert again (e.g. to compare with the local voice).

How the Gemini engine differs:
- **Segments are about a paragraph long** (up to 1400 characters) for more natural flow.
- **Quality check:** each segment's audio length is checked against its text, with one retry. Whisper QA runs only with the local engine.
- **If Google rejects the key or billing isn't enabled,** the job pauses with Google's message. Fix the key, then press Resume.

## Troubleshooting

| Message | What to do |
|---|---|
| "API key not valid" | Re-copy the key from aistudio.google.com/apikey |
| mentions billing | Enable billing on the key's project |
| "rate limit / quota exceeded" | Wait, lower `MYTTS_GEMINI_CONCURRENCY`, or raise your tier |
| The voice sounds slightly different between paragraphs | This is a known Gemini trait. Keep the style text short and consistent, and use the same voice/model for the whole book |
