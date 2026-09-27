<script lang="ts">
  import type { GeminiVoiceInfo } from "../types";
  import Icon from "../icons/Icon.svelte";

  let {
    voice,
    selected,
    previewing,
    playing,
    onSelect,
    onPreview,
  }: {
    voice: GeminiVoiceInfo;
    selected: boolean;
    previewing: boolean;
    playing: boolean;
    onSelect: () => void;
    onPreview: () => void;
  } = $props();
</script>

<div
  role="button"
  tabindex="0"
  class="text-left rounded-2xl border p-4 flex items-center justify-between gap-2 transition-colors cursor-pointer
    {selected ? 'border-accent bg-accent-soft' : 'border-border bg-surface hover:border-accent/40'}"
  onclick={onSelect}
  onkeydown={(e) => e.key === "Enter" && onSelect()}
>
  <div class="min-w-0">
    <div class="font-medium text-sm truncate">{voice.name}</div>
    <div class="text-xs text-muted mt-0.5 flex items-center gap-1.5">
      {#if voice.gender}<Icon name={voice.gender} size={12} class="shrink-0" />{/if}
      <span>{voice.style}</span>
    </div>
  </div>
  <button
    class="w-9 h-9 rounded-full flex items-center justify-center bg-surface-2 hover:bg-accent hover:text-accent-fg transition-colors shrink-0"
    onclick={(e) => { e.stopPropagation(); onPreview(); }}
    disabled={previewing}
    aria-label={playing ? "Playing preview" : "Play preview"}
  >
    {#if previewing}
      <Icon name="loader" size={14} class="animate-spin" />
    {:else}
      <Icon name={playing ? "pause" : "play"} size={14} />
    {/if}
  </button>
</div>
