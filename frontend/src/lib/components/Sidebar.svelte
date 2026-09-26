<script lang="ts">
  import { jobsStore } from "../stores/jobs.svelte";
  import { uiStore } from "../stores/ui.svelte";
  import JobCard from "./JobCard.svelte";
  import Icon from "../icons/Icon.svelte";
</script>

{#if uiStore.sidebarOpen}
  <div
    class="fixed inset-0 z-30 bg-black/40 min-[960px]:hidden"
    role="presentation"
    onclick={() => uiStore.closeSidebar()}
  ></div>
{/if}

<aside
  class="z-40 border-r border-border bg-surface flex flex-col h-full w-72
    max-[959px]:fixed max-[959px]:inset-y-0 max-[959px]:left-0 max-[959px]:max-w-[85vw]
    max-[959px]:shadow-lift max-[959px]:transition-transform max-[959px]:duration-200
    {uiStore.sidebarOpen ? 'max-[959px]:translate-x-0' : 'max-[959px]:-translate-x-full'}
    min-[960px]:shrink-0"
>
  <div class="p-3">
    <button
      class="w-full flex items-center justify-center gap-2 rounded-xl bg-accent text-accent-fg font-medium text-sm py-2.5 shadow-soft hover:bg-accent-hover transition-colors"
      onclick={() => { uiStore.startNewBook(); }}
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
