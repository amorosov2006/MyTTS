// Bottom player: plays finished chapters directly, and plays an in-progress chapter
// segment-by-segment (inserting pause_after_ms of silence) until it switches to the
// assembled chapter file once it's done. See docs/API.md "Processing order guarantee".
import * as api from "../api";
import { chapterAudioUrl } from "../api";
import { jobsStore } from "./jobs.svelte";
import { segmentsStore } from "./segments.svelte";
import type { ChapterState, SegmentEventData } from "../types";

type Mode = "idle" | "segments" | "final";

const POS_KEY = "mytts.player.positions";

function loadPositions(): Record<string, { chapter: number; time: number }> {
  try {
    return JSON.parse(localStorage.getItem(POS_KEY) || "{}");
  } catch {
    return {};
  }
}

function savePosition(jobId: string, chapter: number, time: number) {
  try {
    const all = loadPositions();
    all[jobId] = { chapter, time };
    localStorage.setItem(POS_KEY, JSON.stringify(all));
  } catch {
    /* ignore */
  }
}

class PlayerStore {
  audioEl: HTMLAudioElement | null = null;

  jobId = $state<string | null>(null);
  chapterIndex = $state<number | null>(null);
  mode = $state<Mode>("idle");
  segIndex = $state(0);
  waiting = $state(false);
  isPlaying = $state(false);
  currentTime = $state(0);
  duration = $state(0);
  rate = $state(1);
  volume = $state(1);
  queueOpen = $state(false);

  job = $derived(this.jobId ? jobsStore.byId[this.jobId] ?? null : null);
  chapter = $derived<ChapterState | null>(
    this.job && this.chapterIndex !== null ? this.job.chapters.find((c) => c.index === this.chapterIndex) ?? null : null
  );
  queue = $derived(this.job ? this.job.chapters.filter((c) => c.include) : []);

  attachAudio(el: HTMLAudioElement) {
    this.audioEl = el;
    el.volume = this.volume;
    el.playbackRate = this.rate;
    el.addEventListener("timeupdate", () => {
      this.currentTime = el.currentTime;
      if (this.jobId && this.chapterIndex !== null && this.mode === "final") {
        savePosition(this.jobId, this.chapterIndex, el.currentTime);
      }
    });
    el.addEventListener("durationchange", () => (this.duration = el.duration || 0));
    el.addEventListener("play", () => (this.isPlaying = true));
    el.addEventListener("pause", () => (this.isPlaying = false));
    el.addEventListener("ended", () => this.onEnded());
  }

  async playChapter(jobId: string, chapterIndex: number, resumeTime = 0) {
    this.jobId = jobId;
    this.chapterIndex = chapterIndex;
    this.segIndex = 0;
    this.waiting = false;
    const ch = jobsStore.byId[jobId]?.chapters.find((c) => c.index === chapterIndex);
    if (ch?.status === "done" && ch.audio_url) {
      this.mode = "final";
      this.setSrc(chapterAudioUrl(jobId, chapterIndex), resumeTime);
    } else {
      this.mode = "segments";
      await segmentsStore.ensureLoaded(jobId, chapterIndex);
      this.playSegmentAt(0);
    }
  }

  private setSrc(url: string, startAt = 0) {
    if (!this.audioEl) return;
    const el = this.audioEl;
    const onReady = () => {
      if (startAt > 0) el.currentTime = startAt;
      el.play().catch(() => {});
      el.removeEventListener("loadedmetadata", onReady);
    };
    el.addEventListener("loadedmetadata", onReady);
    el.src = url;
    el.load();
  }

  private playSegmentAt(i: number) {
    if (!this.jobId || this.chapterIndex === null || !this.audioEl) return;
    const list = segmentsStore.get(this.jobId, this.chapterIndex);
    if (i < list.length) {
      this.waiting = false;
      this.segIndex = i;
      const el = this.audioEl;
      el.src = list[i].url;
      el.load();
      el.play().catch(() => {});
    } else {
      const ch = this.chapter;
      if (ch?.status === "done" && ch.audio_url) {
        this.switchToFinal();
      } else {
        this.waiting = true;
        this.isPlaying = false;
      }
    }
  }

  private elapsedFromSegments(uptoExclusive: number): number {
    if (!this.jobId || this.chapterIndex === null) return 0;
    const list = segmentsStore.get(this.jobId, this.chapterIndex);
    let t = 0;
    for (let i = 0; i < Math.min(uptoExclusive, list.length); i++) {
      t += list[i].duration_s + (list[i].pause_after_ms || 0) / 1000;
    }
    return t;
  }

  private switchToFinal() {
    if (!this.jobId || this.chapterIndex === null) return;
    const approx = this.elapsedFromSegments(this.segIndex);
    this.mode = "final";
    this.setSrc(chapterAudioUrl(this.jobId, this.chapterIndex), approx);
  }

  /** Called by the SSE layer when a `segment` event for the active job arrives. */
  onSegmentEvent(jobId: string, data: SegmentEventData) {
    if (jobId !== this.jobId || data.chapter !== this.chapterIndex) return;
    if (this.mode === "segments" && this.waiting) this.playSegmentAt(this.segIndex);
  }

  /** Called by the SSE layer when a `chapter` event for the active job arrives. */
  onChapterEvent(jobId: string, chapter: ChapterState) {
    if (jobId !== this.jobId || chapter.index !== this.chapterIndex) return;
    if (chapter.status === "done" && this.mode === "segments" && this.waiting) this.switchToFinal();
  }

  private onEnded() {
    if (this.mode === "segments") {
      const list = this.jobId && this.chapterIndex !== null ? segmentsStore.get(this.jobId, this.chapterIndex) : [];
      const pauseMs = list[this.segIndex]?.pause_after_ms || 0;
      setTimeout(() => this.playSegmentAt(this.segIndex + 1), pauseMs);
    } else {
      this.nextChapter();
    }
  }

  nextChapter() {
    if (!this.job || this.chapterIndex === null) return;
    const idx = this.queue.findIndex((c) => c.index === this.chapterIndex);
    const next = this.queue[idx + 1];
    if (next) this.playChapter(this.job.id, next.index);
  }

  prevChapter() {
    if (!this.job || this.chapterIndex === null) return;
    if (this.audioEl && this.audioEl.currentTime > 3) {
      this.audioEl.currentTime = 0;
      return;
    }
    const idx = this.queue.findIndex((c) => c.index === this.chapterIndex);
    const prev = this.queue[idx - 1];
    if (prev) this.playChapter(this.job.id, prev.index);
  }

  toggle() {
    if (!this.audioEl) return;
    if (this.audioEl.paused) this.audioEl.play().catch(() => {});
    else this.audioEl.pause();
  }

  seekBy(deltaSeconds: number) {
    if (!this.audioEl) return;
    this.audioEl.currentTime = Math.max(0, this.audioEl.currentTime + deltaSeconds);
  }

  seekTo(seconds: number) {
    if (!this.audioEl) return;
    this.audioEl.currentTime = seconds;
  }

  setRate(r: number) {
    this.rate = r;
    if (this.audioEl) this.audioEl.playbackRate = r;
  }

  setVolume(v: number) {
    this.volume = v;
    if (this.audioEl) this.audioEl.volume = v;
  }

  resumePositionFor(jobId: string): { chapter: number; time: number } | null {
    return loadPositions()[jobId] ?? null;
  }
}

export const playerStore = new PlayerStore();
