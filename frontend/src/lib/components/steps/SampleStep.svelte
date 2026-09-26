<script lang="ts">
  import type { JobInfo, SampleInfo } from "../../types";
  import * as api from "../../api";
  import { jobsStore } from "../../stores/jobs.svelte";
  import { samplesStore } from "../../stores/samples.svelte";
  import { toastStore } from "../../stores/toasts.svelte";
  import { voicesStore } from "../../stores/voices.svelte";
  import Icon from "../../icons/Icon.svelte";
  import { formatClock } from "../../format";

  let { job }: { job: JobInfo } = $props();

  const includedChapters = $derived(job.chapters.filter((c) => c.include));
  const middleChapter = $derived(includedChapters[Math.floor(includedChapters.length / 2)]?.index ?? 0);

  let chapterChoice = $state<number | "middle">("middle");
  let offset = $state(0.5);
  let seconds = $state<30 | 60 | 120>(60);
  let customText = $state("");
  let useCustomText = $state(false);
  let requesting = $state(false);

  $effect(() => {
    samplesStore.refresh(job.id);
  });

  const samples = $derived([...samplesStore.forJob(job.id)].sort((a, b) => b.created_at - a.created_at));
  const latest = $derived(samples[0] ?? null);

  let liveAudio: HTMLAudioElement | null = $state(null);
  let liveSegIndex = $state(0);
  let livePlayingId = $state<string | null>(null);

  function watchLive(sample: SampleInfo) {
    if (livePlayingId !== sample.id) return;
    if (liveSegIndex < sample.segments.length) {
      const el = liveAudio;
      if (!el) return;
      el.src = sample.segments[liveSegIndex];
      el.play().catch(() => {});
    }
  }

  $effect(() => {
    if (latest) watchLive(latest);
  });

  function startLivePlayback(sample: SampleInfo) {
    livePlayingId = sample.id;
    liveSegIndex = 0;
    watchLive(sample);
  }

  function onLiveEnded() {
    liveSegIndex += 1;
    const sample = samples.find((s) => s.id === livePlayingId);
    if (sample) watchLive(sample);
  }

  async function renderSample() {
    requesting = true;
    try {
      const req: any = { seconds };
      if (useCustomText && customText.trim()) {
        req.text = customText.trim();
      } else {
        req.chapter = chapterChoice === "middle" ? middleChapter : chapterChoice;
        req.offset = offset;
      }
      const sample = await api.requestSample(job.id, req);
      samplesStore.upsert(sample);
      startLivePlayback(sample);
    } catch (err: any) {
      toastStore.error(err.detail || "Could not render a sample.");
    } finally {
      requesting = false;
    }
  }

  async function approve(sampleId: string) {
    try {
      const updated = await api.approveSample(job.id, sampleId);
      jobsStore.upsert(updated);
      toastStore.success("Sample approved.");
    } catch (err: any) {
      toastStore.error(err.detail || "Could not approve this sample.");
    }
  }

  function voiceName(id: string) {
    return voicesStore.byId(id)?.name ?? id;
  }
</script>

<div class="max-w-3xl mx-auto p-6 space-y-6">
  <div class="rounded-2xl border border-border bg-surface p-6 text-center">
    <h2 class="text-lg font-semibold mb-1">Render a sample from the middle of the book</h2>
    <p class="text-sm text-muted mb-5">Hear exactly how the book will sound — same voice, style and settings — before converting everything.</p>

    <button
      class="inline-flex items-center gap-2 rounded-xl bg-accent text-accent-fg font-medium px-5 py-3 hover:bg-accent-hover transition-colors disabled:opacity-60"
      onclick={renderSample}
      disabled={requesting}
    >
      {#if requesting}<Icon name="loader" size={16} class="animate-spin" />{:else}<Icon name="sparkles" size={16} />{/if}
      Render sample
    </button>

    <div class="mt-6 grid sm:grid-cols-2 gap-4 text-left">
      <div>
        <label class="block text-sm font-medium mb-1" for="chap">Chapter</label>
        <select id="chap" class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm" bind:value={chapterChoice} disabled={useCustomText}>
          <option value="middle">Middle chapter (default)</option>
          {#each includedChapters as c}
            <option value={c.index}>{c.title || `Chapter ${c.index + 1}`}</option>
          {/each}
        </select>
      </div>
      <div>
        <label class="block text-sm font-medium mb-1" for="len">Length</label>
        <select id="len" class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm" bind:value={seconds} disabled={useCustomText}>
          <option value={30}>30 s</option>
          <option value={60}>60 s</option>
          <option value={120}>120 s</option>
        </select>
      </div>
      <div class="sm:col-span-2">
        <div class="flex justify-between text-sm mb-1">
          <label for="pos" class="font-medium">Position in chapter</label>
          <span class="text-muted">{Math.round(offset * 100)}%</span>
        </div>
        <input id="pos" type="range" min="0" max="1" step="0.05" bind:value={offset} disabled={useCustomText} class="w-full accent-[var(--color-accent)]" />
      </div>
      <div class="sm:col-span-2">
        <label class="flex items-center gap-2 text-sm mb-1">
          <input type="checkbox" bind:checked={useCustomText} class="accent-[var(--color-accent)]" />
          Use custom text instead
        </label>
        {#if useCustomText}
          <textarea class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm resize-none" rows="3" bind:value={customText} placeholder="Paste any text to preview the voice…"></textarea>
        {/if}
      </div>
    </div>
  </div>

  {#if job.sample_approved}
    <div class="flex items-center gap-2 rounded-xl border border-success/30 bg-success-soft text-success px-4 py-3 text-sm">
      <Icon name="check-circle" size={16} />
      This sample is approved for the current settings. You're ready to convert.
    </div>
  {:else if samples.length > 0}
    <div class="flex items-center gap-2 rounded-xl border border-warning/30 bg-warning-soft text-warning px-4 py-3 text-sm">
      <Icon name="alert-triangle" size={16} />
      Settings changed since your approved sample — render a new one to hear the current settings.
    </div>
  {/if}

  <audio bind:this={liveAudio} onended={onLiveEnded} class="hidden"></audio>

  {#if samples.length}
    <div>
      <h3 class="text-sm font-semibold text-muted uppercase tracking-wide mb-2">Sample history</h3>
      <div class="rounded-2xl border border-border bg-surface divide-y divide-border overflow-hidden">
        {#each samples as s (s.id)}
          <div class="px-4 py-3 flex items-center gap-3">
            <button
              class="w-9 h-9 rounded-full flex items-center justify-center bg-surface-2 hover:bg-accent hover:text-accent-fg transition-colors shrink-0 disabled:opacity-40"
              disabled={s.status !== "done"}
              onclick={() => {
                if (s.audio_url) {
                  livePlayingId = null;
                  liveAudio!.src = s.audio_url;
                  liveAudio!.play().catch(() => {});
                }
              }}
              aria-label="Play sample"
            >
              {#if s.status === "done"}<Icon name="play" size={13} />{:else}<Icon name="loader" size={13} class="animate-spin" />{/if}
            </button>
            <div class="min-w-0 flex-1">
              <div class="text-sm truncate">{voiceName(s.settings.voice_id)} · {s.settings.speed.toFixed(2)}× · exp {s.settings.params.temperature.toFixed(2)}</div>
              <div class="text-xs text-muted">
                {s.status === "done" ? formatClock(s.duration_s) : s.status}
                · {new Date(s.created_at * 1000).toLocaleTimeString()}
              </div>
            </div>
            {#if s.status === "done"}
              <button class="text-xs font-medium text-accent hover:underline shrink-0" onclick={() => approve(s.id)}>
                Sounds good ✓
              </button>
            {/if}
          </div>
        {/each}
      </div>
      {#if latest}
        <div class="mt-3 rounded-xl border border-border bg-surface-2 px-4 py-3 text-sm text-muted italic">
          “{latest.text.slice(0, 320)}{latest.text.length > 320 ? "…" : ""}”
        </div>
      {/if}
    </div>
  {/if}
</div>
