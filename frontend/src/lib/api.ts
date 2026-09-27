// Typed client for the MyTTS HTTP API (docs/API.md). All paths are relative — the dev server
// proxies /api to the mock/real backend, and in production it's the same origin.
import type {
  ChapterPatch,
  ChapterText,
  EngineInfo,
  GeminiKeyStatus,
  JobInfo,
  JobSettings,
  Lang,
  SampleInfo,
  SampleRequest,
  SegmentRef,
  SystemInfo,
  Voice,
} from "./types";

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return (await res.json()) as T;
  return undefined as T;
}

function json(body: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

// ----------------------------------------------------------------------- system

export const getHealth = () => request<{ ok: boolean; version: string }>("/health");
export const getSystem = () => request<SystemInfo>("/system");
export const pickFolder = (start?: string) =>
  request<{ path: string | null }>("/pick-folder", json({ start }));
export const getFormats = () => request<{ extensions: string[] }>("/formats");

// ----------------------------------------------------------------------- voices

export const listVoices = () => request<Voice[]>("/voices");
export const voicePreviewUrl = (id: string) => `/api/voices/${id}/preview`;

export const designVoice = (payload: { name: string; lang: string; description: string; gender?: string }) =>
  request<{ voice_id: string }>("/voices/design", json(payload));

export const cloneVoice = (payload: {
  audio: File;
  transcript: string;
  name: string;
  lang: string;
  gender?: string;
}) => {
  const form = new FormData();
  form.append("audio", payload.audio);
  form.append("transcript", payload.transcript);
  form.append("name", payload.name);
  form.append("lang", payload.lang);
  if (payload.gender) form.append("gender", payload.gender);
  return request<Voice>("/voices/clone", { method: "POST", body: form });
};

export const deleteVoice = (id: string) => request<void>(`/voices/${id}`, { method: "DELETE" });

// ----------------------------------------------------------------------- jobs

export const uploadJob = (file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<JobInfo>("/jobs", { method: "POST", body: form });
};

export const listJobs = () => request<JobInfo[]>("/jobs");
export const getJob = (id: string) => request<JobInfo>(`/jobs/${id}`);
export const deleteJob = (id: string) => request<void>(`/jobs/${id}`, { method: "DELETE" });
export const jobCoverUrl = (id: string) => `/api/jobs/${id}/cover`;

export const getChapterText = (jobId: string, index: number) =>
  request<ChapterText>(`/jobs/${jobId}/chapters/${index}/text`);

export const patchChapters = (jobId: string, patches: ChapterPatch[]) =>
  request<JobInfo>(`/jobs/${jobId}/chapters`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patches) });

export const putSettings = (jobId: string, settings: JobSettings) =>
  request<JobInfo>(`/jobs/${jobId}/settings`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) });

export const requestSample = (jobId: string, req: SampleRequest) =>
  request<SampleInfo>(`/jobs/${jobId}/samples`, json(req));

export const listSamples = (jobId: string) => request<SampleInfo[]>(`/jobs/${jobId}/samples`);

export const approveSample = (jobId: string, sampleId: string) =>
  request<JobInfo>(`/jobs/${jobId}/samples/${sampleId}/approve`, { method: "POST" });

export const sampleAudioUrl = (jobId: string, sampleId: string) => `/api/jobs/${jobId}/samples/${sampleId}/audio`;

export const startJob = (jobId: string) => request<JobInfo>(`/jobs/${jobId}/start`, { method: "POST" });
export const pauseJob = (jobId: string) => request<JobInfo>(`/jobs/${jobId}/pause`, { method: "POST" });
export const resumeJob = (jobId: string) => request<JobInfo>(`/jobs/${jobId}/resume`, { method: "POST" });
export const cancelJob = (jobId: string) => request<JobInfo>(`/jobs/${jobId}/cancel`, { method: "POST" });
export const revealJob = (jobId: string) => request<void>(`/jobs/${jobId}/reveal`, { method: "POST" });
export const duplicateJob = (jobId: string) => request<JobInfo>(`/jobs/${jobId}/duplicate`, { method: "POST" });

export const segmentAudioUrl = (jobId: string, segmentId: string) => `/api/jobs/${jobId}/segments/${segmentId}/audio`;
export const chapterAudioUrl = (jobId: string, index: number) => `/api/jobs/${jobId}/chapters/${index}/audio`;
export const getChapterSegments = (jobId: string, index: number) =>
  request<SegmentRef[]>(`/jobs/${jobId}/chapters/${index}/segments`);

// ----------------------------------------------------------------------- engines / Gemini

export const getEngines = () => request<EngineInfo[]>("/engines");

export const getGeminiKeyStatus = () => request<GeminiKeyStatus>("/keys/gemini");

export const putGeminiKey = (api_key: string) =>
  request<GeminiKeyStatus>("/keys/gemini", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key }) });

export const deleteGeminiKey = () => request<void>("/keys/gemini", { method: "DELETE" });

/** POST /api/engines/gemini/preview returns audio/wav — fetched as a blob for the caller to turn into an object URL. */
export async function previewGeminiVoice(payload: { voice: string; lang: Lang; style?: string; model?: string }): Promise<Blob> {
  const res = await fetch(`/api/engines/gemini/preview`, json(payload));
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.blob();
}
