// Mirrors the Pydantic models in mytts/contracts.py — see docs/API.md for the HTTP surface.

export type Lang = "ru" | "en";

export type ChapterKind = "body" | "front" | "back" | "notes" | "toc";

export interface ChapterText {
  title: string;
  paragraphs: string[];
}

export interface ChapterPatch {
  index: number;
  title?: string;
  include?: boolean;
}

export interface Voice {
  id: string;
  name: string;
  lang: Lang;
  gender?: "male" | "female" | null;
  ref_text: string;
  description: string;
  builtin: boolean;
  /** The API replaces `ref_audio` with a servable URL. */
  preview_url: string;
}

export interface SynthesisParams {
  temperature: number;
  top_p: number;
  repetition_penalty: number;
}

export type OutputFormat = "mp3" | "m4b" | "both";

export interface JobSettings {
  voice_id: string;
  lang: Lang | null;
  speed: number;
  params: SynthesisParams;
  output_dir: string;
  output_format: OutputFormat;
  bitrate: string;
  qa: boolean;
  read_titles: boolean;
  skip_footnotes: boolean;
  pause_paragraph_ms: number;
  pause_sentence_ms: number;
}

export type JobStatus = "parsed" | "queued" | "running" | "paused" | "done" | "failed" | "cancelled";

export type ChapterRunStatus = "pending" | "running" | "assembling" | "done" | "failed" | "skipped";

export interface ChapterState {
  index: number;
  title: string;
  include: boolean;
  chars: number;
  kind: ChapterKind;
  segments_total: number;
  segments_done: number;
  status: ChapterRunStatus;
  audio_url: string | null;
  duration_s: number;
}

export interface Progress {
  segments_total: number;
  segments_done: number;
  audio_s: number;
  elapsed_s: number;
  eta_s: number | null;
  x_realtime: number | null;
}

export interface JobInfo {
  id: string;
  status: JobStatus;
  title: string;
  author: string | null;
  lang: Lang | null;
  source_format: string;
  has_cover: boolean;
  created_at: number;
  settings: JobSettings;
  chapters: ChapterState[];
  progress: Progress;
  output_path: string | null;
  warnings: string[];
  error: string | null;
  sample_approved: boolean;
}

export interface SampleRequest {
  chapter?: number;
  offset?: number;
  seconds?: number;
  text?: string;
}

export type SampleStatus = "queued" | "running" | "done" | "failed";

export interface SampleInfo {
  id: string;
  job_id: string;
  status: SampleStatus;
  settings: JobSettings;
  text: string;
  segments: string[];
  audio_url: string | null;
  duration_s: number;
  created_at: number;
  error: string | null;
}

export type WorkerState = "stopped" | "starting" | "idle" | "busy" | "restarting" | "failed";

export interface WorkerStatus {
  state: WorkerState;
  footprint_gb: number;
  system_available_gb: number;
  restarts: number;
  model: string | null;
  message: string | null;
}

export interface SystemInfo {
  worker: WorkerStatus;
  memory: { total_gb: number; available_gb: number; app_footprint_gb: number; cap_gb: number };
  defaults: JobSettings;
  output_dir_default: string;
}

export interface SegmentRef {
  segment_id: string;
  url: string;
  duration_s: number;
  pause_after_ms: number;
}

export interface SegmentEventData {
  segment_id: string;
  chapter: number;
  index: number;
  url: string;
  duration_s: number;
  cer: number | null;
}

export interface LogEventData {
  level: "info" | "warning" | "error";
  message: string;
}

export interface VoiceEventData {
  voice: Voice;
  status: "ready" | "failed";
  error?: string;
}

export type SseEvent =
  | { type: "job"; job_id: string; data: JobInfo }
  | { type: "segment"; job_id: string; data: SegmentEventData }
  | { type: "chapter"; job_id: string; data: ChapterState }
  | { type: "sample"; job_id: string; data: SampleInfo }
  | { type: "voice"; job_id: null; data: VoiceEventData }
  | { type: "worker"; job_id: null; data: WorkerStatus }
  | { type: "log"; job_id: string | null; data: LogEventData };

export interface ApiError {
  detail: string;
}
