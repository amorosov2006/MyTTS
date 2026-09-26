#!/usr/bin/env node
// Mock backend for MyTTS frontend development. Plain Node, no dependencies.
// Implements docs/API.md against realistic fake data so the UI can be built
// and demoed without the real FastAPI/Qwen3-TTS backend.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const VOICES_DIR = path.join(REPO_ROOT, "voices");
const PORT = 8750;

// ----------------------------------------------------------------------- utils

const now = () => Date.now() / 1000;
const uid = (p) => `${p}_${crypto.randomBytes(4).toString("hex")}`;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const CHARS_PER_SECOND = 14.0;

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

function parseMultipart(buf, contentType) {
  const m = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(contentType || "");
  const boundary = m ? m[1] || m[2] : null;
  if (!boundary) return [];
  const boundaryBuf = Buffer.from(`--${boundary}`);
  const parts = [];
  let start = buf.indexOf(boundaryBuf);
  while (start !== -1) {
    const next = buf.indexOf(boundaryBuf, start + boundaryBuf.length);
    if (next === -1) break;
    let chunk = buf.slice(start + boundaryBuf.length, next);
    // strip leading CRLF and trailing CRLF before next boundary marker
    if (chunk.slice(0, 2).toString() === "\r\n") chunk = chunk.slice(2);
    if (chunk.slice(-2).toString() === "\r\n") chunk = chunk.slice(0, -2);
    if (chunk.length && chunk.slice(0, 2).toString() !== "--") {
      const headerEnd = chunk.indexOf("\r\n\r\n");
      if (headerEnd !== -1) {
        const rawHeaders = chunk.slice(0, headerEnd).toString("utf8");
        const data = chunk.slice(headerEnd + 4);
        const nameMatch = /name="([^"]+)"/.exec(rawHeaders);
        const filenameMatch = /filename="([^"]*)"/.exec(rawHeaders);
        const ctMatch = /Content-Type:\s*([^\r\n]+)/i.exec(rawHeaders);
        parts.push({
          name: nameMatch ? nameMatch[1] : "",
          filename: filenameMatch ? filenameMatch[1] : undefined,
          contentType: ctMatch ? ctMatch[1].trim() : undefined,
          data,
        });
      }
    }
    start = next;
  }
  return parts;
}

function sendJson(res, status, body) {
  const s = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(s),
  });
  res.end(s);
}

function sendFile(res, filePath, contentType, req) {
  if (!fs.existsSync(filePath)) return sendJson(res, 404, { detail: "not found" });
  const stat = fs.statSync(filePath);
  const range = req?.headers?.range;
  if (range) {
    const m = /bytes=(\d*)-(\d*)/.exec(range);
    if (m) {
      let s = m[1] ? parseInt(m[1], 10) : 0;
      let e = m[2] ? parseInt(m[2], 10) : stat.size - 1;
      e = Math.min(e, stat.size - 1);
      if (s <= e) {
        res.writeHead(206, {
          "Content-Type": contentType,
          "Content-Range": `bytes ${s}-${e}/${stat.size}`,
          "Accept-Ranges": "bytes",
          "Content-Length": e - s + 1,
        });
        fs.createReadStream(filePath, { start: s, end: e }).pipe(res);
        return;
      }
    }
  }
  res.writeHead(200, {
    "Content-Type": contentType,
    "Accept-Ranges": "bytes",
    "Content-Length": stat.size,
  });
  fs.createReadStream(filePath).pipe(res);
}

// ----------------------------------------------------------------------- lorem text

const RU_SENTENCES = [
  "Дождь стучал по крыше, и весь дом, казалось, погрузился в глубокую задумчивость.",
  "Она подошла к окну и долго смотрела на пустую улицу, освещённую жёлтым фонарём.",
  "В комнате пахло старыми книгами и остывшим чаем.",
  "Никто не решался заговорить первым, и тишина тянулась, как густой мёд.",
  "За рекой начинался лес, тёмный и незнакомый, будто из старой сказки.",
  "Он вспомнил тот далёкий летний день, когда всё ещё было просто.",
  "Поезд тронулся, и перрон медленно поплыл назад, унося с собой прощальные взгляды.",
  "Городские огни отражались в мокром асфальте, дробясь на тысячи маленьких искр.",
  "Бабушка всегда говорила, что терпение важнее силы.",
  "Ветер принёс запах моря, хотя до берега было ещё далеко.",
  "На столе лежала недописанная записка, и почерк выдавал сильное волнение.",
  "Собака подняла голову, будто почуяла что-то вдалеке, и насторожилась.",
  "Свет свечи дрожал, и тени на стенах оживали, складываясь в странные фигуры.",
  "Он знал, что решение придётся принимать одному, и это пугало сильнее всего.",
  "Утро выдалось морозным, и снег скрипел под ногами так звонко, что слышно было на всю улицу.",
];

const EN_SENTENCES = [
  "The rain kept falling long after the last lamp in the street went dark.",
  "She stood by the window, watching the fog roll in from the harbor.",
  "The old library smelled of dust and forgotten summers.",
  "Nobody spoke for a long while, and the silence grew heavier by the minute.",
  "Beyond the river the forest waited, dark and unfamiliar.",
  "He remembered that distant afternoon when everything still made sense.",
  "The train pulled away, and the platform slid backward into memory.",
  "City lights shimmered in the wet pavement, breaking into a thousand small sparks.",
  "Grandmother always said patience mattered more than strength.",
  "A salt wind carried the smell of the sea, though the shore was still far off.",
  "An unfinished note lay on the table, the handwriting betraying real worry.",
  "The dog lifted its head, sensing something far away, and went still.",
  "Candlelight trembled, and the shadows on the wall folded into strange shapes.",
  "He knew the decision would be his alone to make, and that frightened him most.",
  "The morning was bitterly cold, and the snow creaked underfoot the whole way home.",
];

function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

function loremParagraph(lang, sentences) {
  const pool = lang === "ru" ? RU_SENTENCES : EN_SENTENCES;
  const out = [];
  for (let i = 0; i < sentences; i++) out.push(pick(pool));
  return out.join(" ");
}

function loremParagraphs(lang, count, sentencesPerParagraph = 4) {
  return Array.from({ length: count }, () => loremParagraph(lang, sentencesPerParagraph));
}

// ----------------------------------------------------------------------- voices

/** @type {Map<string, any>} voiceId -> voice record { ...Voice, refPath, mime } */
const voices = new Map();

function loadBuiltinVoices() {
  if (!fs.existsSync(VOICES_DIR)) return;
  for (const dir of fs.readdirSync(VOICES_DIR)) {
    const jsonPath = path.join(VOICES_DIR, dir, "voice.json");
    const wavPath = path.join(VOICES_DIR, dir, "ref.wav");
    if (fs.existsSync(jsonPath)) {
      const meta = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
      voices.set(meta.id, { ...meta, refPath: fs.existsSync(wavPath) ? wavPath : null, mime: "audio/wav" });
    }
  }
}
loadBuiltinVoices();

function voiceForApi(v) {
  const { refPath, mime, ...rest } = v;
  delete rest.ref_audio;
  return { ...rest, preview_url: `/api/voices/${v.id}/preview` };
}

function builtinRefFor(lang, gender) {
  for (const v of voices.values()) {
    if (v.builtin && v.lang === lang && (!gender || v.gender === gender)) return v;
  }
  for (const v of voices.values()) if (v.builtin && v.lang === lang) return v;
  return [...voices.values()][0];
}

// ----------------------------------------------------------------------- worker / system state

const worker = {
  state: "idle",
  footprint_gb: 0,
  system_available_gb: 22.4,
  restarts: 0,
  model: "Qwen3-TTS-12Hz-1.7B-6bit",
  message: null,
};

function tickWorker() {
  const busy = [...jobs.values()].some((j) => j.status === "running") ||
    [...samples.values()].some((s) => s.status === "running") ||
    designingVoice;
  worker.state = busy ? "busy" : "idle";
  const target = busy ? 9.6 : 0.4;
  worker.footprint_gb += (target - worker.footprint_gb) * 0.3 + (Math.random() - 0.5) * 0.1;
  worker.footprint_gb = Math.max(0, worker.footprint_gb);
  worker.system_available_gb = clamp(22.4 - worker.footprint_gb * 0.15 + (Math.random() - 0.5) * 0.3, 10, 30);
}
setInterval(() => {
  tickWorker();
  broadcast({ type: "worker", job_id: null, data: { ...worker } });
}, 2000);

let designingVoice = false;

function defaultSettings(lang) {
  return {
    voice_id: lang === "ru" ? "ru_male" : "en_male",
    lang: null,
    speed: 1.0,
    params: { temperature: 0.8, top_p: 1.0, repetition_penalty: 1.05 },
    output_dir: outputDirDefault,
    output_format: "mp3",
    bitrate: "96k",
    qa: true,
    read_titles: true,
    skip_footnotes: true,
    pause_paragraph_ms: 700,
    pause_sentence_ms: 250,
  };
}

const outputDirDefault = path.join(os.homedir(), "Audiobooks");

// ----------------------------------------------------------------------- jobs

const SUPPORTED_EXTENSIONS = [
  ".epub", ".fb2", ".fb2.zip", ".docx", ".pdf", ".txt", ".md",
  ".rtf", ".odt", ".doc", ".html", ".mobi", ".azw3",
];

/** @type {Map<string, any>} */
const jobs = new Map();
/** @type {Map<string, any>} */
const samples = new Map();
/** @type {Map<string, {jobId:string, refPath:string, mime:string, duration_s:number}>} */
const segmentFiles = new Map();

const RU_AUTHORS = ["Михаил Осокин", "Анна Ветрова"];
const EN_AUTHORS = ["Elena Whitfield", "Nathaniel Cross"];

const CHAPTER_PLAN = [
  { kind: "front", ru: "Титульный лист", en: "Title Page", size: "tiny" },
  { kind: "toc", ru: "Оглавление", en: "Contents", size: "tiny" },
  { kind: "body", ru: "Пролог", en: "Prologue", size: "med" },
  { kind: "body", ru: "Глава 1", en: "Chapter 1", size: "big" },
  { kind: "body", ru: "Глава 2", en: "Chapter 2", size: "big" },
  { kind: "body", ru: "Глава 3", en: "Chapter 3", size: "big" },
  { kind: "body", ru: "Глава 4", en: "Chapter 4", size: "big" },
  { kind: "body", ru: "Глава 5", en: "Chapter 5", size: "big" },
  { kind: "body", ru: "Глава 6", en: "Chapter 6", size: "big" },
  { kind: "body", ru: "Глава 7", en: "Chapter 7", size: "big" },
  { kind: "notes", ru: "Примечания", en: "Notes", size: "small" },
  { kind: "back", ru: "Об авторе", en: "About the Author", size: "tiny" },
];

function sizeToParagraphs(size) {
  switch (size) {
    case "tiny": return 1;
    case "small": return 3;
    case "med": return 6;
    default: return 12;
  }
}

function segmentsTotalFor(chars) {
  return Math.max(1, Math.ceil(chars / 220));
}

function buildBook(filename) {
  const lower = filename.toLowerCase();
  const lang = lower.includes("ru") ? "ru" : "en";
  const ext = SUPPORTED_EXTENSIONS.find((e) => lower.endsWith(e)) || path.extname(lower) || ".txt";
  const base = path.basename(filename, path.extname(filename)).replace(/[_-]+/g, " ").trim();
  const title = base.replace(/\b\w/g, (c) => c.toUpperCase()) || (lang === "ru" ? "Безымянная книга" : "Untitled Book");
  const author = pick(lang === "ru" ? RU_AUTHORS : EN_AUTHORS);

  const chapters = CHAPTER_PLAN.map((c, index) => {
    const paragraphs = loremParagraphs(lang, sizeToParagraphs(c.size), c.size === "big" ? 4 : 3);
    return {
      index,
      title: lang === "ru" ? c.ru : c.en,
      paragraphs,
      include: c.kind === "body",
      kind: c.kind,
      chars: paragraphs.join("").length,
    };
  });

  const warnings = [];
  if (ext === ".pdf") warnings.push(lang === "ru" ? "PDF без текстового слоя — использовано OCR" : "PDF had no text layer, OCR used");

  return { title, author, lang, source_format: ext.replace(/^\./, "").replace(".zip", ""), chapters, warnings };
}

function chapterStateFor(ch) {
  return {
    index: ch.index,
    title: ch.title,
    include: ch.include,
    chars: ch.chars,
    segments_total: ch.include ? segmentsTotalFor(ch.chars) : 0,
    segments_done: 0,
    status: "pending",
    audio_url: null,
    duration_s: 0,
    // Not part of mytts/contracts.py's ChapterState — included as a mock-only convenience
    // so the Chapters step can show a kind badge (front/toc/notes/back/body).
    kind: ch.kind,
  };
}

function jobToApi(job) {
  return {
    id: job.id,
    status: job.status,
    title: job.title,
    author: job.author,
    lang: job.lang,
    source_format: job.source_format,
    has_cover: false,
    created_at: job.created_at,
    settings: job.settings,
    chapters: job.chapters.map((c) => ({ ...c })),
    progress: { ...job.progress },
    output_path: job.output_path,
    warnings: job.warnings,
    error: job.error,
    sample_approved: job.sample_approved,
  };
}

const AUDIBLE_KEYS = [
  "voice_id", "lang", "speed", "params", "read_titles", "skip_footnotes",
  "pause_paragraph_ms", "pause_sentence_ms",
];

function settingsAudibleEqual(a, b) {
  return AUDIBLE_KEYS.every((k) => JSON.stringify(a[k]) === JSON.stringify(b[k]));
}

// ----------------------------------------------------------------------- SSE

/** @type {Set<http.ServerResponse>} */
const globalClients = new Set();
/** @type {Map<string, Set<http.ServerResponse>>} */
const jobClients = new Map();
const lastJobBroadcast = new Map(); // jobId -> {t, status}

function sseHeaders(res) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });
}

function writeEvent(res, event) {
  res.write(`event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
}

function broadcast(event) {
  for (const res of globalClients) writeEvent(res, event);
  if (event.job_id) {
    const set = jobClients.get(event.job_id);
    if (set) for (const res of set) writeEvent(res, event);
  }
}

function broadcastJobThrottled(job, force = false) {
  const key = job.id;
  const last = lastJobBroadcast.get(key);
  const t = Date.now();
  if (force || !last || last.status !== job.status || t - last.t >= 1000) {
    lastJobBroadcast.set(key, { t, status: job.status });
    broadcast({ type: "job", job_id: job.id, data: jobToApi(job) });
  }
}

function log(job, level, message) {
  broadcast({ type: "log", job_id: job ? job.id : null, data: { level, message } });
}

// ----------------------------------------------------------------------- conversion simulation

function registerSegment(jobId, refPath, mime, duration_s) {
  const id = uid("seg");
  segmentFiles.set(id, { jobId, refPath, mime, duration_s });
  return id;
}

function tickJob(job) {
  if (job.status !== "running") return;
  const included = job.chapters.filter((c) => c.include);
  let chapter = included.find((c) => c.status !== "done" && c.status !== "skipped");
  if (!chapter) {
    job.status = "done";
    job.output_path = path.join(job.settings.output_dir || outputDirDefault, `${job.author} - ${job.title}`);
    log(job, "info", "Conversion complete.");
    broadcastJobThrottled(job, true);
    clearInterval(job.timer);
    job.timer = null;
    return;
  }
  if (chapter.status === "pending") {
    chapter.status = "running";
    broadcast({ type: "chapter", job_id: job.id, data: { ...chapter } });
  }
  if (chapter.status === "running") {
    if (chapter.segments_done < chapter.segments_total) {
      const idx = chapter.segments_done;
      const voice = voices.get(job.settings.voice_id) || builtinRefFor(job.lang || "en");
      const remainingChars = Math.max(60, chapter.chars - idx * 220);
      const segChars = Math.min(220, remainingChars);
      const duration_s = Number(((segChars / CHARS_PER_SECOND) / job.settings.speed).toFixed(2));
      const segment_id = registerSegment(job.id, voice.refPath, voice.mime, duration_s);
      chapter.segments_done += 1;
      job.progress.segments_done += 1;
      job.progress.audio_s += duration_s;
      job.progress.elapsed_s = now() - job.startedAt + job.pausedOffset;
      job.progress.x_realtime = job.progress.elapsed_s > 0 ? Number((job.progress.audio_s / job.progress.elapsed_s).toFixed(2)) : null;
      const remaining = job.progress.segments_total - job.progress.segments_done;
      const perSeg = job.progress.segments_done > 0 ? job.progress.elapsed_s / job.progress.segments_done : 0.3;
      job.progress.eta_s = Number((remaining * perSeg).toFixed(1));
      const cer = job.settings.qa ? Number((Math.random() * 0.05).toFixed(3)) : null;
      if (job.settings.qa && Math.random() < 0.08) {
        log(job, "warning", `Segment ${segment_id} needed a retry (CER above threshold), regenerated.`);
      }
      const segData = { segment_id, chapter: chapter.index, index: idx, url: `/api/jobs/${job.id}/segments/${segment_id}/audio`, duration_s, cer, pause_after_ms: job.settings.pause_sentence_ms };
      job.segmentLog[chapter.index].push(segData);
      broadcast({ type: "segment", job_id: job.id, data: segData });
      chapter.duration_s += duration_s;
      broadcastJobThrottled(job);
    } else {
      chapter.status = "assembling";
      broadcast({ type: "chapter", job_id: job.id, data: { ...chapter } });
    }
  } else if (chapter.status === "assembling") {
    chapter.status = "done";
    chapter.audio_url = `/api/jobs/${job.id}/chapters/${chapter.index}/audio`;
    broadcast({ type: "chapter", job_id: job.id, data: { ...chapter } });
    broadcastJobThrottled(job, true);
  }
}

function startJobTimer(job) {
  if (job.timer) return;
  job.timer = setInterval(() => tickJob(job), 260);
}

// ----------------------------------------------------------------------- sample simulation

function tickSample(sample, job) {
  if (sample.status !== "running") return;
  if (sample.pos < sample.text.length) {
    const chunk = sample.text.slice(sample.pos, sample.pos + 180);
    sample.pos += chunk.length;
    const duration_s = Number((chunk.length / CHARS_PER_SECOND / sample.settings.speed).toFixed(2));
    const voice = voices.get(sample.settings.voice_id) || builtinRefFor(job.lang || "en");
    const segment_id = registerSegment(job.id, voice.refPath, voice.mime, duration_s);
    sample.segments.push(`/api/jobs/${job.id}/segments/${segment_id}/audio`);
    sample.duration_s += duration_s;
    broadcast({ type: "sample", job_id: job.id, data: { ...sample } });
  } else {
    sample.status = "done";
    sample.audio_url = `/api/jobs/${job.id}/samples/${sample.id}/audio`;
    const voice = voices.get(sample.settings.voice_id) || builtinRefFor(job.lang || "en");
    sample.refPath = voice.refPath;
    sample.mime = voice.mime;
    broadcast({ type: "sample", job_id: job.id, data: { ...sample } });
    clearInterval(sample.timer);
    sample.timer = null;
  }
}

function middleIncludedChapter(job) {
  const included = job.chapters.filter((c) => c.include);
  if (!included.length) return job.chapters[0];
  return included[Math.floor(included.length / 2)];
}

// ----------------------------------------------------------------------- HTTP routes

const server = http.createServer(async (req, res) => {
  try {
    const u = new URL(req.url, `http://${req.headers.host}`);
    const p = u.pathname;
    const method = req.method;

    // -------- system
    if (method === "GET" && p === "/api/health") return sendJson(res, 200, { ok: true, version: "0.1.0" });

    if (method === "GET" && p === "/api/system") {
      return sendJson(res, 200, {
        worker: { ...worker },
        memory: {
          total_gb: 48,
          available_gb: Number(worker.system_available_gb.toFixed(1)),
          app_footprint_gb: Number((worker.footprint_gb + 0.6).toFixed(1)),
          cap_gb: 30,
        },
        defaults: defaultSettings("ru"),
        output_dir_default: outputDirDefault,
      });
    }

    if (method === "POST" && p === "/api/pick-folder") {
      await readBody(req);
      return sendJson(res, 200, { path: outputDirDefault });
    }

    if (method === "GET" && p === "/api/formats") {
      return sendJson(res, 200, { extensions: SUPPORTED_EXTENSIONS });
    }

    // -------- voices
    if (method === "GET" && p === "/api/voices") {
      return sendJson(res, 200, [...voices.values()].map(voiceForApi));
    }

    let m;
    if (method === "GET" && (m = /^\/api\/voices\/([^/]+)\/preview$/.exec(p))) {
      const v = voices.get(m[1]);
      if (!v || !v.refPath) return sendJson(res, 404, { detail: "voice not found" });
      return sendFile(res, v.refPath, v.mime || "audio/wav", req);
    }

    if (method === "POST" && p === "/api/voices/design") {
      const runningJob = [...jobs.values()].some((j) => j.status === "running");
      if (runningJob) return sendJson(res, 409, { detail: "Cannot design a voice while a job is running." });
      const body = JSON.parse((await readBody(req)).toString("utf8") || "{}");
      const voice_id = uid("voice");
      designingVoice = true;
      sendJson(res, 202, { voice_id });
      setTimeout(() => {
        const ref = builtinRefFor(body.lang, body.gender);
        const voice = {
          id: voice_id, name: body.name, lang: body.lang, gender: body.gender || null,
          ref_text: ref ? ref.ref_text : "", description: body.description || "", builtin: false,
          refPath: ref ? ref.refPath : null, mime: "audio/wav",
        };
        voices.set(voice_id, voice);
        designingVoice = false;
        broadcast({ type: "voice", job_id: null, data: { voice: voiceForApi(voice), status: "ready" } });
      }, 2200);
      return;
    }

    if (method === "POST" && p === "/api/voices/clone") {
      const buf = await readBody(req);
      const parts = parseMultipart(buf, req.headers["content-type"]);
      const field = (n) => parts.find((x) => x.name === n)?.data?.toString("utf8");
      const audioPart = parts.find((x) => x.name === "audio");
      const name = field("name") || "Cloned voice";
      const lang = field("lang") || "en";
      const transcript = field("transcript") || "";
      const gender = field("gender") || null;
      if (!audioPart) return sendJson(res, 422, { detail: "Missing audio file" });
      const voice_id = uid("voice");
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mytts-clone-"));
      const ext = audioPart.filename ? path.extname(audioPart.filename) || ".wav" : ".wav";
      const filePath = path.join(dir, `ref${ext}`);
      fs.writeFileSync(filePath, audioPart.data);
      const mime = audioPart.contentType || "audio/wav";
      const voice = { id: voice_id, name, lang, gender, ref_text: transcript, description: "", builtin: false, refPath: filePath, mime };
      voices.set(voice_id, voice);
      return sendJson(res, 201, voiceForApi(voice));
    }

    if (method === "DELETE" && (m = /^\/api\/voices\/([^/]+)$/.exec(p))) {
      const v = voices.get(m[1]);
      if (!v) return sendJson(res, 404, { detail: "not found" });
      if (v.builtin) return sendJson(res, 403, { detail: "Built-in voices cannot be deleted." });
      voices.delete(m[1]);
      res.writeHead(204); return res.end();
    }

    // -------- jobs
    if (method === "POST" && p === "/api/jobs") {
      const buf = await readBody(req);
      const parts = parseMultipart(buf, req.headers["content-type"]);
      const filePart = parts.find((x) => x.name === "file");
      if (!filePart || !filePart.filename) return sendJson(res, 422, { detail: "No file uploaded." });
      const lower = filePart.filename.toLowerCase();
      if (!SUPPORTED_EXTENSIONS.some((e) => lower.endsWith(e))) {
        return sendJson(res, 422, { detail: `Unsupported file format: ${path.extname(filePart.filename)}` });
      }
      if (/corrupt|drm|bad/.test(lower)) {
        return sendJson(res, 422, { detail: "Could not read this file: it appears to be corrupted or DRM-protected." });
      }
      const book = buildBook(filePart.filename);
      const id = uid("j");
      const chapters = book.chapters.map(chapterStateFor);
      const job = {
        id, status: "parsed", title: book.title, author: book.author, lang: book.lang,
        source_format: book.source_format, created_at: now(), settings: defaultSettings(book.lang),
        chapters, rawChapters: book.chapters,
        progress: { segments_total: chapters.filter((c) => c.include).reduce((a, c) => a + c.segments_total, 0), segments_done: 0, audio_s: 0, elapsed_s: 0, eta_s: null, x_realtime: null },
        output_path: null, warnings: book.warnings, error: null, sample_approved: false,
        timer: null, startedAt: 0, pausedOffset: 0,
        segmentLog: chapters.map(() => []),
      };
      jobs.set(id, job);
      log(job, "info", `Parsed "${book.title}" — ${chapters.length} chapters.`);
      return sendJson(res, 201, jobToApi(job));
    }

    if (method === "GET" && p === "/api/jobs") {
      return sendJson(res, 200, [...jobs.values()].sort((a, b) => b.created_at - a.created_at).map(jobToApi));
    }

    if (method === "GET" && (m = /^\/api\/jobs\/([^/]+)$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      return sendJson(res, 200, jobToApi(job));
    }

    if (method === "DELETE" && (m = /^\/api\/jobs\/([^/]+)$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      if (job.timer) clearInterval(job.timer);
      jobs.delete(m[1]);
      res.writeHead(204); return res.end();
    }

    if (method === "GET" && (m = /^\/api\/jobs\/([^/]+)\/cover$/.exec(p))) {
      return sendJson(res, 404, { detail: "no cover" });
    }

    if (method === "GET" && (m = /^\/api\/jobs\/([^/]+)\/chapters\/(\d+)\/text$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      const ch = job.rawChapters[Number(m[2])];
      if (!ch) return sendJson(res, 404, { detail: "chapter not found" });
      return sendJson(res, 200, { title: ch.title, paragraphs: ch.paragraphs });
    }

    if (method === "PATCH" && (m = /^\/api\/jobs\/([^/]+)\/chapters$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      if (!["parsed", "paused"].includes(job.status)) return sendJson(res, 409, { detail: "Chapters can only be edited while parsed or paused." });
      const patches = JSON.parse((await readBody(req)).toString("utf8") || "[]");
      for (const patch of patches) {
        const ch = job.chapters.find((c) => c.index === patch.index);
        if (!ch) continue;
        if (patch.title !== undefined) { ch.title = patch.title; job.rawChapters[patch.index].title = patch.title; }
        if (patch.include !== undefined) {
          ch.include = patch.include;
          ch.segments_total = ch.include ? segmentsTotalFor(ch.chars) : 0;
        }
      }
      job.progress.segments_total = job.chapters.filter((c) => c.include).reduce((a, c) => a + c.segments_total, 0);
      return sendJson(res, 200, jobToApi(job));
    }

    if (method === "PUT" && (m = /^\/api\/jobs\/([^/]+)\/settings$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      if (!["parsed", "paused"].includes(job.status)) return sendJson(res, 409, { detail: "Settings can only change while parsed or paused." });
      const next = JSON.parse((await readBody(req)).toString("utf8") || "{}");
      const wasAudibleEqual = settingsAudibleEqual(job.settings, next);
      job.settings = { ...job.settings, ...next };
      if (!wasAudibleEqual) job.sample_approved = false;
      return sendJson(res, 200, jobToApi(job));
    }

    // -------- samples
    if (method === "POST" && (m = /^\/api\/jobs\/([^/]+)\/samples$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      const body = JSON.parse((await readBody(req)).toString("utf8") || "{}");
      const chapterIdx = body.chapter ?? middleIncludedChapter(job).index;
      const raw = job.rawChapters[chapterIdx] || job.rawChapters[0];
      const fullText = body.text || raw.paragraphs.join(" ");
      const seconds = body.seconds || 60;
      const targetChars = Math.round(seconds * CHARS_PER_SECOND);
      const offset = body.offset ?? 0.5;
      const startPos = Math.max(0, Math.floor((fullText.length - targetChars) * offset));
      const text = fullText.slice(startPos, startPos + targetChars) || fullText.slice(0, targetChars) || fullText;
      const id = uid("smp");
      const sample = {
        id, job_id: job.id, status: "running", settings: { ...job.settings }, text,
        segments: [], audio_url: null, duration_s: 0, created_at: now(), error: null,
        pos: 0, timer: null,
      };
      if (!samples.has(job.id)) samples.set(job.id, []);
      samples.get(job.id).push(sample);
      sample.timer = setInterval(() => tickSample(sample, job), 350);
      const { pos, timer, ...pub } = sample;
      return sendJson(res, 202, pub);
    }

    if (method === "GET" && (m = /^\/api\/jobs\/([^/]+)\/samples$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      const list = (samples.get(job.id) || []).map(({ pos, timer, refPath, mime, ...pub }) => pub);
      return sendJson(res, 200, list);
    }

    if (method === "POST" && (m = /^\/api\/jobs\/([^/]+)\/samples\/([^/]+)\/approve$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      const sample = (samples.get(job.id) || []).find((s) => s.id === m[2]);
      if (!sample) return sendJson(res, 404, { detail: "sample not found" });
      job.sample_approved = settingsAudibleEqual(sample.settings, job.settings);
      return sendJson(res, 200, jobToApi(job));
    }

    if (method === "GET" && (m = /^\/api\/jobs\/([^/]+)\/samples\/([^/]+)\/audio$/.exec(p))) {
      const job = jobs.get(m[1]);
      const sample = job && (samples.get(job.id) || []).find((s) => s.id === m[2]);
      if (!sample || !sample.refPath) return sendJson(res, 404, { detail: "sample audio not ready" });
      return sendFile(res, sample.refPath, sample.mime || "audio/wav", req);
    }

    // -------- conversion control
    if (method === "POST" && (m = /^\/api\/jobs\/([^/]+)\/start$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      job.status = "queued";
      job.startedAt = now();
      job.pausedOffset = 0;
      broadcastJobThrottled(job, true);
      setTimeout(() => {
        if (job.status === "queued") {
          job.status = "running";
          log(job, "info", "Started conversion.");
          broadcastJobThrottled(job, true);
          startJobTimer(job);
        }
      }, 500);
      return sendJson(res, 200, jobToApi(job));
    }

    if (method === "POST" && (m = /^\/api\/jobs\/([^/]+)\/pause$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      if (job.timer) { clearInterval(job.timer); job.timer = null; }
      job.pausedOffset = job.progress.elapsed_s;
      job.status = "paused";
      broadcastJobThrottled(job, true);
      return sendJson(res, 200, jobToApi(job));
    }

    if (method === "POST" && (m = /^\/api\/jobs\/([^/]+)\/resume$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      job.status = "running";
      job.startedAt = now();
      broadcastJobThrottled(job, true);
      startJobTimer(job);
      return sendJson(res, 200, jobToApi(job));
    }

    if (method === "POST" && (m = /^\/api\/jobs\/([^/]+)\/cancel$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      if (job.timer) { clearInterval(job.timer); job.timer = null; }
      job.status = "cancelled";
      broadcastJobThrottled(job, true);
      return sendJson(res, 200, jobToApi(job));
    }

    // -------- audio & segment listing
    if (method === "GET" && (m = /^\/api\/jobs\/([^/]+)\/segments\/([^/]+)\/audio$/.exec(p))) {
      const seg = segmentFiles.get(m[2]);
      if (!seg || !seg.refPath) return sendJson(res, 404, { detail: "segment not found" });
      return sendFile(res, seg.refPath, seg.mime || "audio/wav", req);
    }

    if (method === "GET" && (m = /^\/api\/jobs\/([^/]+)\/chapters\/(\d+)\/audio$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      const ch = job.chapters[Number(m[2])];
      if (!ch || ch.status !== "done") return sendJson(res, 404, { detail: "chapter audio not ready" });
      const voice = voices.get(job.settings.voice_id) || builtinRefFor(job.lang || "en");
      return sendFile(res, voice.refPath, voice.mime || "audio/wav", req);
    }

    if (method === "GET" && (m = /^\/api\/jobs\/([^/]+)\/chapters\/(\d+)\/segments$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      const chIdx = Number(m[2]);
      const list = (job.segmentLog?.[chIdx] || []).map(({ segment_id, url, duration_s, pause_after_ms }) => ({ segment_id, url, duration_s, pause_after_ms }));
      return sendJson(res, 200, list);
    }

    // -------- SSE
    if (method === "GET" && p === "/api/events") {
      sseHeaders(res);
      globalClients.add(res);
      for (const job of jobs.values()) writeEvent(res, { type: "job", job_id: job.id, data: jobToApi(job) });
      writeEvent(res, { type: "worker", job_id: null, data: { ...worker } });
      const keep = setInterval(() => res.write(": keep-alive\n\n"), 15000);
      req.on("close", () => { clearInterval(keep); globalClients.delete(res); });
      return;
    }

    if (method === "GET" && (m = /^\/api\/jobs\/([^/]+)\/events$/.exec(p))) {
      const job = jobs.get(m[1]);
      if (!job) return sendJson(res, 404, { detail: "job not found" });
      sseHeaders(res);
      if (!jobClients.has(job.id)) jobClients.set(job.id, new Set());
      jobClients.get(job.id).add(res);
      writeEvent(res, { type: "job", job_id: job.id, data: jobToApi(job) });
      writeEvent(res, { type: "worker", job_id: null, data: { ...worker } });
      const keep = setInterval(() => res.write(": keep-alive\n\n"), 15000);
      req.on("close", () => { clearInterval(keep); jobClients.get(job.id)?.delete(res); });
      return;
    }

    sendJson(res, 404, { detail: "not found" });
  } catch (err) {
    console.error(err);
    sendJson(res, 500, { detail: String(err?.message || err) });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[mock] MyTTS mock backend listening on http://127.0.0.1:${PORT}`);
  console.log(`[mock] Loaded ${voices.size} built-in voices: ${[...voices.keys()].join(", ")}`);
});
