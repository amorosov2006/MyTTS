<script lang="ts">
  import type { StepId } from "../stores/ui.svelte";
  import Icon from "../icons/Icon.svelte";

  let { active, sampleApproved, onSelect }: { active: StepId; sampleApproved: boolean; onSelect: (s: StepId) => void } = $props();

  const steps: { id: StepId; label: string; icon: string }[] = [
    { id: "book", label: "Book", icon: "book" },
    { id: "chapters", label: "Chapters", icon: "queue" },
    { id: "voice", label: "Voice & style", icon: "mic" },
    { id: "sample", label: "Sample", icon: "sparkles" },
    { id: "convert", label: "Convert", icon: "music" },
  ];
</script>

<div class="flex items-center gap-1 border-b border-border px-4 overflow-x-auto">
  {#each steps as s, i (s.id)}
    <button
      class="relative flex items-center gap-1.5 px-3.5 py-3 text-sm font-medium whitespace-nowrap transition-colors
        {active === s.id ? 'text-accent' : 'text-muted hover:text-fg'}"
      onclick={() => onSelect(s.id)}
    >
      <span class="w-5 h-5 rounded-full bg-surface-2 flex items-center justify-center text-[11px] font-semibold">{i + 1}</span>
      <Icon name={s.icon} size={15} />
      {s.label}
      {#if s.id === "sample" && sampleApproved}
        <Icon name="check-circle" size={13} class="text-success" />
      {/if}
      {#if active === s.id}
        <span class="absolute left-2 right-2 -bottom-px h-0.5 rounded-full bg-accent"></span>
      {/if}
    </button>
  {/each}
</div>
