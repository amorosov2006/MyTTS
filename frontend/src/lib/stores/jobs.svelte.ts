import * as api from "../api";
import type { ChapterState, JobInfo } from "../types";

class JobsStore {
  byId = $state<Record<string, JobInfo>>({});
  selectedId = $state<string | null>(null);
  loading = $state(false);

  list = $derived(Object.values(this.byId).sort((a, b) => b.created_at - a.created_at));
  selected = $derived(this.selectedId ? this.byId[this.selectedId] ?? null : null);

  async refresh() {
    this.loading = true;
    try {
      const jobs = await api.listJobs();
      const next: Record<string, JobInfo> = {};
      for (const j of jobs) next[j.id] = j;
      this.byId = next;
      if (!this.selectedId && jobs.length) this.selectedId = jobs[0].id;
    } finally {
      this.loading = false;
    }
  }

  upsert(job: JobInfo) {
    this.byId = { ...this.byId, [job.id]: job };
  }

  remove(id: string) {
    const { [id]: _, ...rest } = this.byId;
    this.byId = rest;
    if (this.selectedId === id) {
      const remaining = Object.values(rest).sort((a, b) => b.created_at - a.created_at);
      this.selectedId = remaining[0]?.id ?? null;
    }
  }

  applyChapter(jobId: string, chapter: ChapterState) {
    const job = this.byId[jobId];
    if (!job) return;
    const chapters = job.chapters.map((c) => (c.index === chapter.index ? chapter : c));
    this.byId = { ...this.byId, [jobId]: { ...job, chapters } };
  }

  select(id: string | null) {
    this.selectedId = id;
  }

  async create(file: File): Promise<JobInfo> {
    const job = await api.uploadJob(file);
    this.upsert(job);
    this.select(job.id);
    return job;
  }

  async deleteJob(id: string) {
    await api.deleteJob(id);
    this.remove(id);
  }
}

export const jobsStore = new JobsStore();
