<script lang="ts">
  import type { JobInfo } from "../../types";
  import * as api from "../../api";
  import { ApiError } from "../../api";
  import { jobsStore } from "../../stores/jobs.svelte";
  import { toastStore } from "../../stores/toasts.svelte";
  import { playerStore } from "../../stores/player.svelte";
  import { uiStore } from "../../stores/ui.svelte";
  import Icon from "../../icons/Icon.svelte";
  import Badge from "../Badge.svelte";
  import { formatClock, formatHours } from "../../format";

  let { job }: { job: JobInfo } = $props();

  let busy = $state(false);
  let revealing = $state(false);

  async function convertAgain() {
    busy = true;
    try {
      const copy = await api.duplicateJob(job.id);
      jobsStore.upsert(copy);
      jobsStore.select(copy.id);
      uiStore.setStep(copy.id, "voice");
      toastStore.success("Copied — pick a new voice or style, then convert.");
    } catch (err: any) {
      toastStore.error(err.detail || "Could not copy the job.");
    } finally {
      busy = false;
    }
  }

  const overallFraction = $derived(job.progress.segments_total > 0 ? job.progress.segments_done / job.progress.segments_total : 0);

  const statusTone: Record<string, "neutral" | "accent" | "success" | "warning" | "danger"> = {
    pending: "neutral",
    running: "accent",
    assembling: "accent",
    done: "success",
    failed: "danger",
    skipped: "neutral",
  };

  const statusRowClass: Record<string, string> = {
    pending: "",
    running: "bg-accent-soft/50",
    assembling: "bg-accent-soft/50",
    done: "",
    failed: "bg-danger-soft/40",
    skipped: "opacity-50",
  };

  const statusBarClass: Record<string, string> = {
    pending: "bg-border",
    running: "bg-accent",
    assembling: "bg-accent",
    done: "bg-success",
    failed: "bg-danger",
    skipped: "bg-border",
  };

  async function start() {
    busy = true;
    try {
      const updated = await api.startJob(job.id);
      jobsStore.upsert(updated);
    } catch (err: any) {
      toastStore.error(err.detail || "Could not start the conversion.");
    } finally {
      busy = false;
    }
  }

  async function pause() {
    busy = true;
    try { jobsStore.upsert(await api.pauseJob(job.id)); } catch (err: any) { toastStore.error(err.detail || "Could not pause."); } finally { busy = false; }
  }
  async function resume() {
    busy = true;
    try { jobsStore.upsert(await api.resumeJob(job.id)); } catch (err: any) { toastStore.error(err.detail || "Could not resume."); } finally { busy = false; }
  }
  async function cancel() {
    if (!confirm("Cancel this conversion? Progress made so far is kept, but the job will stop.")) return;
    busy = true;
    try { jobsStore.upsert(await api.cancelJob(job.id)); } catch (err: any) { toastStore.error(err.detail || "Could not cancel."); } finally { busy = false; }
  }

  function playChapter(index: number) {
    if (job.id) playerStore.playChapter(job.id, index);
  }

  async function reveal() {
    revealing = true;
    try {
      await api.revealJob(job.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        toastStore.error("Output folder not created yet.");
      } else {
        toastStore.error((err as any)?.detail || "Could not open the output folder.");
      }
    } finally {
      revealing = false;
    }
  }
</script>

<div class="max-w-3xl mx-auto p-6 space-y-6">
  {#if job.status === "parsed"}
    <div class="rounded-2xl border border-border bg-surface p-8 text-center">
      <button
        class="inline-flex items-center gap-2 rounded-xl bg-accent text-accent-fg font-semibold px-6 py-3.5 text-base hover:bg-accent-hover transition-colors disabled:opacity-60"
        onclick={start}
        disabled={busy}
      >
        <Icon name="play" size={16} /> Start conversion
      </button>
    </div>
  {:else}
    <div class="rounded-2xl border border-border bg-surface p-5">
      <div class="flex items-center justify-between mb-3">
        <div class="flex items-center gap-2">
          <Badge tone={job.status === "done" ? "success" : job.status === "failed" ? "danger" : "accent"}>
            {job.status}
          </Badge>
          {#if job.progress.x_realtime}
            <span class="text-sm text-muted">{job.progress.x_realtime.toFixed(1)}× realtime</span>
          {/if}
        </div>
        <div class="flex gap-2">
          {#if job.status === "running" || job.status === "queued"}
            <button class="rounded-lg border border-border px-3 py-1.5 text-sm hover:border-accent" onclick={pause} disabled={busy}>Pause</button>
            <button class="rounded-lg border border-border px-3 py-1.5 text-sm hover:border-danger hover:text-danger" onclick={cancel} disabled={busy}>Cancel</button>
          {:else if job.status === "paused"}
            <button class="rounded-lg bg-accent text-accent-fg px-3 py-1.5 text-sm hover:bg-accent-hover" onclick={resume} disabled={busy}>Resume</button>
            <button class="rounded-lg border border-border px-3 py-1.5 text-sm hover:border-danger hover:text-danger" onclick={cancel} disabled={busy}>Cancel</button>
          {/if}
        </div>
      </div>

      <div class="h-2.5 rounded-full bg-surface-2 overflow-hidden">
        <div class="h-full bg-accent transition-all duration-300" style="width:{overallFraction * 100}%"></div>
      </div>

      <div class="mt-3 grid grid-cols-3 gap-3 text-sm">
        <div><span class="text-muted block text-xs">Audio produced</span>{formatClock(job.progress.audio_s)}</div>
        <div><span class="text-muted block text-xs">ETA</span>{job.progress.eta_s != null ? formatClock(job.progress.eta_s) : "—"}</div>
        <div><span class="text-muted block text-xs">Segments</span>{job.progress.segments_done} / {job.progress.segments_total}</div>
      </div>

      {#if job.output_path}
        <div class="mt-3 pt-3 border-t border-border text-xs text-muted flex items-center gap-1.5 flex-wrap">
          <Icon name="folder" size={13} class="shrink-0" />
          <span class="min-w-0">Saved to <span class="font-mono break-all">{job.output_path}</span></span>
          <button
            class="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs text-fg hover:border-accent hover:text-accent transition-colors disabled:opacity-50 shrink-0"
            onclick={reveal}
            disabled={revealing}
          >
            <Icon name="folder" size={12} /> Show in Finder
          </button>
        </div>
      {/if}
      {#if job.status === "done"}
        <div class="mt-2 text-sm text-success flex items-center gap-1.5"><Icon name="check-circle" size={14} /> Conversion complete — {formatHours(job.progress.audio_s)} of audio.</div>
      {/if}
      {#if job.status === "done" || job.status === "cancelled"}
        <button
          class="mt-3 rounded-lg border border-border px-3 py-1.5 text-sm hover:border-accent hover:text-accent"
          onclick={convertAgain}
          disabled={busy}
        >Convert again with different settings…</button>
      {/if}
    </div>
  {/if}

  <div>
    <h3 class="text-sm font-semibold text-muted uppercase tracking-wide mb-2">Chapters</h3>
    <div class="rounded-2xl border border-border bg-surface divide-y divide-border overflow-hidden">
      {#each job.chapters.filter((c) => c.include) as ch (ch.index)}
        <div class="px-4 py-3 flex items-center gap-3 transition-colors {statusRowClass[ch.status] ?? ''}">
          <button
            class="w-8 h-8 rounded-full flex items-center justify-center bg-surface-2 hover:bg-accent hover:text-accent-fg transition-colors shrink-0 disabled:opacity-30"
            disabled={ch.status === "pending" || ch.status === "skipped"}
            onclick={() => playChapter(ch.index)}
            aria-label="Play chapter"
          >
            <Icon name="play" size={12} />
          </button>
          <div class="min-w-0 flex-1">
            <div class="text-sm font-medium truncate flex items-center gap-1.5" title={ch.title || `Chapter ${ch.index + 1}`}>
              {#if ch.status === "running" || ch.status === "assembling"}
                <span class="relative flex h-2 w-2 shrink-0" aria-hidden="true">
                  <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-60"></span>
                  <span class="relative inline-flex rounded-full h-2 w-2 bg-accent"></span>
                </span>
              {/if}
              <span class="truncate">{ch.title || `Chapter ${ch.index + 1}`}</span>
            </div>
            <div class="h-1.5 rounded-full bg-surface-2 overflow-hidden mt-1 max-w-xs">
              <div
                class="h-full transition-all duration-300 {statusBarClass[ch.status] ?? 'bg-accent'} {ch.status === 'running' ? 'animate-pulse' : ''}"
                style="width:{ch.segments_total ? (ch.segments_done / ch.segments_total) * 100 : ch.status === 'done' ? 100 : 0}%"
              ></div>
            </div>
            {#if ch.segments_total > 0 && ch.status !== "done" && ch.status !== "skipped"}
              <div class="text-[11px] text-muted mt-0.5 tabular-nums">{ch.segments_done} / {ch.segments_total} segments</div>
            {/if}
          </div>
          <div class="text-xs text-muted w-16 text-right shrink-0 tabular-nums">{ch.status === "done" && ch.duration_s ? formatClock(ch.duration_s) : "—"}</div>
          <Badge tone={statusTone[ch.status]}>{ch.status}</Badge>
        </div>
      {/each}
    </div>
  </div>
</div>
