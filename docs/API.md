# MyTTS HTTP API (v1)

- **Base URL:** `http://127.0.0.1:8750`. All endpoints are under `/api`.
- **Bodies:** JSON unless noted.
- **Models:** the shapes are the Pydantic models in `mytts/contracts.py`.
- **Errors:** `{"detail": "<human readable>"}` with a 4xx or 5xx status.
- **Static:** the built frontend is served from `/`.

## System

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/health` | — | `{"ok": true, "version": "0.1.0"}` |
| GET | `/api/system` | — | See below |
| POST | `/api/pick-folder` | `{"start": "<path>?"}` | `{"path": "/Users/.../Audiobooks"}`, or `{"path": null}` if cancelled |
| GET | `/api/formats` | — | `{"extensions": [".epub", ".fb2", ...]}` |

The `/api/system` response:

```
{worker: WorkerStatus, memory: {total_gb, available_gb, app_footprint_gb, cap_gb: 30},
 defaults: JobSettings, output_dir_default: str}
```

`/api/pick-folder` opens the native macOS folder dialog via osascript.

## Voices

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/voices` | — | `Voice[]` (built-in + user). `ref_audio` is replaced by `preview_url` |
| GET | `/api/voices/{id}/preview` | — | `audio/wav` (the reference clip) |
| POST | `/api/voices/design` | `{"name", "lang", "description", "gender"?}` | `202 {"voice_id"}`. Result arrives as a `voice` event on `/api/events` |
| POST | `/api/voices/clone` | multipart: `audio` (wav/mp3/m4a, 5–30 s), `transcript`, `name`, `lang`, `gender`? | `201 Voice` |
| DELETE | `/api/voices/{id}` | — | `204`. User voices only; built-in voices return `403` |

- **Voice design** renders a reference sentence for the voice's language with the VoiceDesign model. It is only allowed when no job is running (`409` otherwise).
- **Voice cloning:** the server converts the uploaded clip to 24 kHz mono WAV.

## Jobs (one job = one uploaded book)

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/jobs` | multipart: `file` | `201 JobInfo` (status `parsed`), or `422 {"detail"}` on an `IngestError` |
| GET | `/api/jobs` | — | `JobInfo[]`, newest first |
| GET | `/api/jobs/{id}` | — | `JobInfo` |
| DELETE | `/api/jobs/{id}` | — | `204`. Removes the job and its work files; **never** the output audio |
| GET | `/api/jobs/{id}/cover` | — | Image, or `404` |
| GET | `/api/jobs/{id}/chapters/{n}/text` | — | `{"title", "paragraphs": [...]}` for the preview pane |
| PATCH | `/api/jobs/{id}/chapters` | `[{"index", "title"?, "include"?}]` | `JobInfo`. Only while status is `parsed` or `paused` |
| PUT | `/api/jobs/{id}/settings` | `JobSettings` | `JobInfo`. Only while `parsed` or `paused`; resets `sample_approved` if any audible setting changed |
| POST | `/api/jobs/{id}/samples` | `SampleRequest` | `202 SampleInfo` |
| GET | `/api/jobs/{id}/samples` | — | `SampleInfo[]` |
| POST | `/api/jobs/{id}/samples/{sid}/approve` | — | `JobInfo` (`sample_approved = true` when the sample's settings equal the current settings) |
| GET | `/api/jobs/{id}/samples/{sid}/audio` | — | `audio/mpeg` |
| POST | `/api/jobs/{id}/start` | — | `JobInfo`. `parsed` → `queued`/`running` |
| POST | `/api/jobs/{id}/pause` | — | `JobInfo` |
| POST | `/api/jobs/{id}/resume` | — | `JobInfo` |
| POST | `/api/jobs/{id}/cancel` | — | `JobInfo` |
| POST | `/api/jobs/{id}/reveal` | — | `204`: opens the book's output folder in Finder, or `404` if it doesn't exist yet |
| GET | `/api/jobs/{id}/samples/{sid}/segments/{segment_id}/audio` | — | `audio/wav`: one processed sample segment, for progressive sample playback |
| GET | `/api/jobs/{id}/segments/{segment_id}/audio` | — | `audio/wav`: the processed segment (trimmed, speed applied), for playing a chapter while it is still being generated |
| GET | `/api/jobs/{id}/chapters/{n}/audio` | — | `audio/mpeg`: the final chapter file, with HTTP Range support |
| GET | `/api/jobs/{id}/chapters/{n}/segments` | — | `[{"segment_id", "url", "duration_s", "pause_after_ms"}]`, the segments completed so far, in order |

- **`POST /api/jobs`** parses the book and creates the job in `parsed` status.
- **Starting a job without an approved sample is allowed.** The UI warns but doesn't block.
- **Samples** are rendered with the current settings. They preempt a running job: sample segments jump the queue.

## Server-Sent Events

- `GET /api/events` streams events for **all** jobs, plus `worker` and `voice` events.
- `GET /api/jobs/{id}/events` streams events for one job only.

Each event looks like this:

```
event: segment
data: {"type":"segment","job_id":"j_abc","data":{"segment_id":"c003s0012","chapter":3,"index":12,"url":"/api/jobs/j_abc/segments/c003s0012/audio","duration_s":7.4,"cer":0.01}}
```

- **On connect:** the server sends a `job` snapshot for each relevant job and a `worker` status.
- **Keep-alive:** a comment line every 15 s.
- **Reconnect:** the client simply reconnects and receives fresh snapshots. `Last-Event-ID` is not needed.

## Processing order guarantee

- **Chapter order:** segments are generated in chapter order (ascending `index` within a chapter), so chapter N+1 starts only after chapter N is fully queued.
  - A `chapter` event with `status: "done"` and an `audio_url` follows once the chapter's MP3 has been assembled.
- **Playing while generating:** the UI plays segments of the current chapter in order via their `url`s, inserting `pause_after_ms` of silence between them. When the chapter's final `audio_url` arrives, the UI switches to it.
