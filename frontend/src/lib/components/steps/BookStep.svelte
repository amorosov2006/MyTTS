<script lang="ts">
  import { jobsStore } from "../../stores/jobs.svelte";
  import { uiStore } from "../../stores/ui.svelte";
  import { formatsStore } from "../../stores/formats.svelte";
  import { toastStore } from "../../stores/toasts.svelte";
  import { ApiError } from "../../api";
  import type { JobInfo } from "../../types";
  import CoverThumb from "../CoverThumb.svelte";
  import Icon from "../../icons/Icon.svelte";
  import { langFlag, langLabel } from "../../format";

  let { job = null }: { job?: JobInfo | null } = $props();

  let dragging = $state(false);
  let uploading = $state(false);
  let errorDetail = $state<string | null>(null);

  $effect(() => {
    formatsStore.refresh();
  });

  async function handleFiles(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    uploading = true;
    errorDetail = null;
    try {
      const created = await jobsStore.create(file);
      uiStore.creatingNew = false;
      uiStore.setStep(created.id, "book");
      toastStore.success(`Parsed "${created.title}".`);
    } catch (err) {
      errorDetail = err instanceof ApiError ? err.detail : "Could not upload this file.";
    } finally {
      uploading = false;
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    dragging = false;
    handleFiles(e.dataTransfer?.files ?? null);
  }

  let fileInput: HTMLInputElement | undefined = $state();
</script>

{#if !job}
  <div class="max-w-2xl mx-auto p-8">
    <h1 class="text-xl font-semibold mb-1">Add a book</h1>
    <p class="text-muted text-sm mb-6">Drop a file to parse it into chapters. Everything runs on this Mac — nothing leaves your computer.</p>

    <div
      role="button"
      tabindex="0"
      class="rounded-2xl border-2 border-dashed transition-colors flex flex-col items-center justify-center gap-3 py-16 px-6 text-center cursor-pointer
        {dragging ? 'border-accent bg-accent-soft' : 'border-border bg-surface hover:border-accent/50'}"
      ondragover={(e) => { e.preventDefault(); dragging = true; }}
      ondragleave={() => (dragging = false)}
      ondrop={onDrop}
      onclick={() => fileInput?.click()}
      onkeydown={(e) => e.key === "Enter" && fileInput?.click()}
    >
      {#if uploading}
        <Icon name="loader" size={32} class="text-accent animate-spin" />
        <div class="text-sm text-muted">Parsing your book…</div>
      {:else}
        <div class="w-14 h-14 rounded-full bg-accent-soft text-accent flex items-center justify-center">
          <Icon name="upload" size={24} />
        </div>
        <div class="font-medium">Drag & drop a book here, or click to choose</div>
        {#if formatsStore.extensions.length}
          <div class="text-xs text-muted max-w-md">{formatsStore.extensions.join("  ·  ")}</div>
        {/if}
      {/if}
    </div>
    <input
      bind:this={fileInput}
      type="file"
      class="hidden"
      accept={formatsStore.extensions.join(",")}
      onchange={(e) => handleFiles((e.target as HTMLInputElement).files)}
    />

    {#if errorDetail}
      <div class="mt-4 flex items-start gap-2 rounded-xl border border-danger/30 bg-danger-soft text-danger px-4 py-3 text-sm">
        <Icon name="alert-triangle" size={16} class="mt-0.5 shrink-0" />
        <div>{errorDetail}</div>
      </div>
    {/if}
  </div>
{:else}
  <div class="max-w-2xl mx-auto p-8">
    <div class="flex gap-5">
      <CoverThumb jobId={job.id} title={job.title} hasCover={job.has_cover} size={128} rounded="rounded-xl" />
      <div class="flex-1 min-w-0">
        <h1 class="text-xl font-semibold truncate">{job.title}</h1>
        <p class="text-muted">{job.author || "Unknown author"}</p>
        <div class="mt-3 flex flex-wrap gap-2 text-sm">
          <span class="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2.5 py-1">
            {langFlag(job.lang)} {langLabel(job.lang)}
          </span>
          <span class="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2.5 py-1 uppercase">
            {job.source_format}
          </span>
          <span class="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2.5 py-1">
            {job.chapters.length} chapters
          </span>
        </div>
      </div>
    </div>

    {#if job.warnings.length}
      <div class="mt-5 space-y-2">
        {#each job.warnings as w}
          <div class="flex items-start gap-2 rounded-xl border border-warning/30 bg-warning-soft text-warning px-4 py-2.5 text-sm">
            <Icon name="alert-triangle" size={15} class="mt-0.5 shrink-0" />
            <div>{w}</div>
          </div>
        {/each}
      </div>
    {/if}

    <button
      class="mt-6 inline-flex items-center gap-2 rounded-xl bg-accent text-accent-fg font-medium px-4 py-2.5 hover:bg-accent-hover transition-colors"
      onclick={() => uiStore.setStep(job!.id, "chapters")}
    >
      Review chapters
      <Icon name="chevron-right" size={16} />
    </button>
  </div>
{/if}
