<script lang="ts">
  import type { JobInfo } from "../types";
  import CoverThumb from "./CoverThumb.svelte";
  import ProgressRing from "./ProgressRing.svelte";
  import Badge from "./Badge.svelte";
  import Icon from "../icons/Icon.svelte";
  import { JOB_STATUS_LABEL } from "../format";
  import { jobsStore } from "../stores/jobs.svelte";
  import { toastStore } from "../stores/toasts.svelte";
  import { uiStore } from "../stores/ui.svelte";

  let { job, selected }: { job: JobInfo; selected: boolean } = $props();

  const tone = $derived(
    job.status === "done" ? "success" : job.status === "failed" || job.status === "cancelled" ? "danger" : job.status === "running" ? "accent" : "neutral"
  );

  const fraction = $derived(job.progress.segments_total > 0 ? job.progress.segments_done / job.progress.segments_total : 0);

  async function remove(e: MouseEvent) {
    e.stopPropagation();
    if (!confirm(`Remove "${job.title}" from the library? This won't delete any audio already saved to disk.`)) return;
    try {
      await jobsStore.deleteJob(job.id);
    } catch (err: any) {
      toastStore.error(err.detail || "Could not remove the book.");
    }
  }
</script>

<div
  role="button"
  tabindex="0"
  class="group w-full text-left rounded-xl p-2.5 flex items-center gap-3 transition-colors border cursor-pointer
    {selected ? 'bg-accent-soft border-accent/40' : 'border-transparent hover:bg-surface-2'}"
  onclick={() => { jobsStore.select(job.id); uiStore.cancelNewBook(); uiStore.closeSidebar(); }}
  onkeydown={(e) => e.key === "Enter" && (jobsStore.select(job.id), uiStore.cancelNewBook(), uiStore.closeSidebar())}
>
  <CoverThumb jobId={job.id} title={job.title} hasCover={job.has_cover} size={44} />
  <div class="min-w-0 flex-1">
    <div class="font-medium text-sm truncate">{job.title}</div>
    <div class="text-xs text-muted truncate">{job.author || "Unknown author"}</div>
    <div class="mt-1">
      <Badge {tone}>{JOB_STATUS_LABEL[job.status]}</Badge>
    </div>
  </div>
  <div class="relative shrink-0 flex items-center">
    {#if job.status === "running" || job.status === "paused"}
      <ProgressRing {fraction} />
    {/if}
    <button
      class="ml-1 p-1 rounded-md text-muted opacity-0 group-hover:opacity-100 hover:bg-surface hover:text-danger transition-opacity"
      onclick={remove}
      aria-label="Remove book"
    >
      <Icon name="trash" size={14} />
    </button>
  </div>
</div>
