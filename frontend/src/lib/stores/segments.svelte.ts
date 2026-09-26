// Per-chapter cache of completed segment audio refs, used by the player to play a chapter
// while it is still being generated. Keyed by "jobId:chapterIndex".
import * as api from "../api";
import type { SegmentEventData, SegmentRef } from "../types";

function key(jobId: string, chapter: number) {
  return `${jobId}:${chapter}`;
}

class SegmentsStore {
  byChapter = $state<Record<string, SegmentRef[]>>({});

  async ensureLoaded(jobId: string, chapter: number) {
    const k = key(jobId, chapter);
    const list = await api.getChapterSegments(jobId, chapter);
    this.byChapter = { ...this.byChapter, [k]: list };
    return list;
  }

  append(jobId: string, data: SegmentEventData) {
    const k = key(jobId, data.chapter);
    const list = this.byChapter[k] ?? [];
    if (list.some((s) => s.segment_id === data.segment_id)) return;
    const ref: SegmentRef = { segment_id: data.segment_id, url: data.url, duration_s: data.duration_s, pause_after_ms: 0 };
    this.byChapter = { ...this.byChapter, [k]: [...list, ref] };
  }

  get(jobId: string, chapter: number): SegmentRef[] {
    return this.byChapter[key(jobId, chapter)] ?? [];
  }
}

export const segmentsStore = new SegmentsStore();
