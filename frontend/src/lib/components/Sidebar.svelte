<script lang="ts">
  import { jobsStore } from "../stores/jobs.svelte";
  import { uiStore } from "../stores/ui.svelte";
  import JobCard from "./JobCard.svelte";
  import Icon from "../icons/Icon.svelte";
</script>

<aside class="w-72 shrink-0 border-r border-border bg-surface flex flex-col h-full">
  <div class="p-3">
    <button
      class="w-full flex items-center justify-center gap-2 rounded-xl bg-accent text-accent-fg font-medium text-sm py-2.5 shadow-soft hover:bg-accent-hover transition-colors"
      onclick={() => uiStore.startNewBook()}
    >
      <Icon name="plus" size={16} />
      New book
    </button>
  </div>
  <div class="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-muted">Library</div>
  <div class="flex-1 overflow-y-auto px-2 pb-3 space-y-1">
    {#if jobsStore.list.length === 0}
      <div class="text-center text-sm text-muted px-4 py-10">
        <Icon name="book" size={28} class="mx-auto mb-2 opacity-50" />
        No books yet. Add one to get started.
      </div>
    {/if}
    {#each jobsStore.list as job (job.id)}
      <JobCard {job} selected={job.id === jobsStore.selectedId && !uiStore.creatingNew} />
    {/each}
  </div>
</aside>
