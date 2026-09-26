<script lang="ts">
  import type { ChapterText, JobInfo } from "../../types";
  import * as api from "../../api";
  import { jobsStore } from "../../stores/jobs.svelte";
  import { toastStore } from "../../stores/toasts.svelte";
  import { CHAPTER_KIND_LABEL, estSecondsFromChars, formatDurationLong } from "../../format";
  import Icon from "../../icons/Icon.svelte";
  import Badge from "../Badge.svelte";

  let { job }: { job: JobInfo } = $props();

  const editable = $derived(job.status === "parsed" || job.status === "paused");

  const included = $derived(job.chapters.filter((c) => c.include));
  const totalChars = $derived(included.reduce((a, c) => a + c.chars, 0));
  const estAudioSeconds = $derived(estSecondsFromChars(totalChars));
  const estConvertSeconds = $derived(estAudioSeconds / 8);

  let editingIndex = $state<number | null>(null);
  let editingValue = $state("");

  let drawerIndex = $state<number | null>(null);
  let drawerText = $state<ChapterText | null>(null);
  let drawerLoading = $state(false);

  async function toggleInclude(index: number, include: boolean) {
    try {
      const updated = await api.patchChapters(job.id, [{ index, include }]);
      jobsStore.upsert(updated);
    } catch (err: any) {
      toastStore.error(err.detail || "Could not update the chapter.");
    }
  }

  function startEdit(index: number, title: string) {
    if (!editable) return;
    editingIndex = index;
    editingValue = title;
  }

  async function commitEdit() {
    if (editingIndex === null) return;
    const index = editingIndex;
    const title = editingValue.trim();
    editingIndex = null;
    if (!title) return;
    try {
      const updated = await api.patchChapters(job.id, [{ index, title }]);
      jobsStore.upsert(updated);
    } catch (err: any) {
      toastStore.error(err.detail || "Could not rename the chapter.");
    }
  }

  async function setAll(include: boolean) {
    try {
      const updated = await api.patchChapters(job.id, job.chapters.map((c) => ({ index: c.index, include })));
      jobsStore.upsert(updated);
    } catch (err: any) {
      toastStore.error(err.detail || "Could not update chapters.");
    }
  }

  async function openDrawer(index: number) {
    drawerIndex = index;
    drawerText = null;
    drawerLoading = true;
    try {
      drawerText = await api.getChapterText(job.id, index);
    } catch (err: any) {
      toastStore.error(err.detail || "Could not load the chapter text.");
    } finally {
      drawerLoading = false;
    }
  }
</script>

<div class="flex h-full">
  <div class="flex-1 overflow-y-auto p-6">
    <div class="max-w-3xl mx-auto">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold">Chapters</h2>
        {#if editable}
          <div class="flex gap-2 text-sm">
            <button class="text-accent hover:underline" onclick={() => setAll(true)}>Select all</button>
            <span class="text-muted">·</span>
            <button class="text-accent hover:underline" onclick={() => setAll(false)}>Select none</button>
          </div>
        {/if}
      </div>

      <div class="rounded-2xl border border-border bg-surface divide-y divide-border overflow-hidden">
        {#each job.chapters as ch (ch.index)}
          <div class="flex items-center gap-3 px-4 py-3 {ch.include ? '' : 'opacity-55'}">
            <input
              type="checkbox"
              checked={ch.include}
              disabled={!editable}
              onchange={(e) => toggleInclude(ch.index, (e.target as HTMLInputElement).checked)}
              class="w-4 h-4 accent-[var(--color-accent)] shrink-0"
            />
            <div class="min-w-0 flex-1">
              {#if editingIndex === ch.index}
                <input
                  class="w-full rounded-md border border-accent px-2 py-1 text-sm bg-surface"
                  bind:value={editingValue}
                  onblur={commitEdit}
                  onkeydown={(e) => e.key === "Enter" && commitEdit()}
                  autofocus
                />
              {:else}
                <button
                  class="text-sm font-medium truncate hover:text-accent text-left {editable ? '' : 'cursor-default'}"
                  onclick={() => startEdit(ch.index, ch.title)}
                  title={editable ? "Click to rename" : undefined}
                >
                  {ch.title || `Chapter ${ch.index + 1}`}
                </button>
              {/if}
              <div class="flex items-center gap-2 mt-0.5">
                <Badge tone={ch.kind === "body" || !ch.kind ? "accent" : "neutral"}>{CHAPTER_KIND_LABEL[ch.kind ?? "body"]}</Badge>
                <span class="text-xs text-muted">{ch.chars.toLocaleString()} chars</span>
                <span class="text-xs text-muted">~{formatDurationLong(estSecondsFromChars(ch.chars))}</span>
              </div>
            </div>
            <button
              class="p-2 rounded-lg text-muted hover:bg-surface-2 hover:text-fg transition-colors shrink-0"
              onclick={() => openDrawer(ch.index)}
              aria-label="Preview text"
            >
              <Icon name="chevron-right" size={16} />
            </button>
          </div>
        {/each}
      </div>

      <div class="mt-5 rounded-2xl border border-border bg-surface-2 px-5 py-4 flex flex-wrap gap-x-8 gap-y-2 text-sm">
        <div><span class="text-muted">Included: </span><span class="font-medium">{included.length} / {job.chapters.length}</span></div>
        <div><span class="text-muted">Estimated audio: </span><span class="font-medium">{formatDurationLong(estAudioSeconds)}</span></div>
        <div><span class="text-muted">Estimated conversion time: </span><span class="font-medium">~{formatDurationLong(estConvertSeconds)}</span> <span class="text-muted">(≈8× realtime)</span></div>
      </div>
    </div>
  </div>

  {#if drawerIndex !== null}
    {@const ch = job.chapters.find((c) => c.index === drawerIndex)}
    <div class="w-96 shrink-0 border-l border-border bg-surface flex flex-col h-full">
      <div class="flex items-center justify-between px-4 py-3 border-b border-border">
        <div class="font-medium text-sm truncate">{ch?.title || `Chapter ${drawerIndex + 1}`}</div>
        <button class="p-1.5 rounded-lg text-muted hover:bg-surface-2" onclick={() => (drawerIndex = null)} aria-label="Close">
          <Icon name="x" size={16} />
        </button>
      </div>
      <div class="flex-1 overflow-y-auto px-4 py-3 space-y-3 text-sm leading-relaxed lang-ru">
        {#if drawerLoading}
          <Icon name="loader" size={20} class="animate-spin text-muted" />
        {:else if drawerText}
          {#each drawerText.paragraphs as p}
            <p>{p}</p>
          {/each}
        {/if}
      </div>
    </div>
  {/if}
</div>
