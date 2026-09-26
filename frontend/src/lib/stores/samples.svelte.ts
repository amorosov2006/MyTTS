import * as api from "../api";
import type { SampleInfo } from "../types";

class SamplesStore {
  byJob = $state<Record<string, SampleInfo[]>>({});

  async refresh(jobId: string) {
    const list = await api.listSamples(jobId);
    this.byJob = { ...this.byJob, [jobId]: list };
  }

  upsert(sample: SampleInfo) {
    const list = this.byJob[sample.job_id] ?? [];
    const idx = list.findIndex((s) => s.id === sample.id);
    const next = idx === -1 ? [...list, sample] : list.map((s) => (s.id === sample.id ? sample : s));
    this.byJob = { ...this.byJob, [sample.job_id]: next };
  }

  forJob(jobId: string) {
    return this.byJob[jobId] ?? [];
  }
}

export const samplesStore = new SamplesStore();
