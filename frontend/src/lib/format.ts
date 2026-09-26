import type { ChapterKind, JobStatus, Lang } from "./types";

const CHARS_PER_SECOND = 14;

export function estSecondsFromChars(chars: number): number {
  return chars / CHARS_PER_SECOND;
}

export function formatClock(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds) || totalSeconds < 0) totalSeconds = 0;
  const s = Math.floor(totalSeconds % 60);
  const m = Math.floor((totalSeconds / 60) % 60);
  const h = Math.floor(totalSeconds / 3600);
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export function formatHours(totalSeconds: number): string {
  const h = totalSeconds / 3600;
  if (h < 1) return `${Math.round(totalSeconds / 60)} min`;
  return `${h.toFixed(1)} h`;
}

export function formatDurationLong(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.round((totalSeconds % 3600) / 60);
  if (h === 0) return `${m}m`;
  return `${h}h ${m}m`;
}

export function langLabel(lang: Lang | null | undefined): string {
  if (lang === "ru") return "RU";
  if (lang === "en") return "EN";
  return "—";
}

export function langFlag(lang: Lang | null | undefined): string {
  if (lang === "ru") return "🇷🇺";
  if (lang === "en") return "🇬🇧";
  return "🌐";
}

export const CHAPTER_KIND_LABEL: Record<ChapterKind, string> = {
  body: "Chapter",
  front: "Front matter",
  back: "Back matter",
  notes: "Notes",
  toc: "Contents",
};

export const JOB_STATUS_LABEL: Record<JobStatus, string> = {
  parsed: "Ready to configure",
  queued: "Queued",
  running: "Converting",
  paused: "Paused",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function initials(title: string): string {
  const words = title.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

// Deterministic warm gradient per string, for cover fallbacks.
const GRADIENTS = [
  ["#d99b6c", "#b5764a"],
  ["#e0b567", "#a3701f"],
  ["#86c79a", "#3f7d52"],
  ["#e08585", "#b23b3b"],
  ["#c9a8e0", "#7f5ba8"],
  ["#7fb6d9", "#3f6fa8"],
];

export function gradientFor(seed: string): [string, string] {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  return GRADIENTS[hash % GRADIENTS.length] as [string, string];
}
