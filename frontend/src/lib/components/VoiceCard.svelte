<script lang="ts">
  import type { Voice } from "../types";
  import Icon from "../icons/Icon.svelte";
  import { langFlag } from "../format";

  let {
    voice,
    selected,
    onSelect,
    onDelete,
  }: { voice: Voice; selected: boolean; onSelect: () => void; onDelete?: () => void } = $props();

  let audioEl: HTMLAudioElement | null = $state(null);
  let playing = $state(false);

  function togglePreview(e: MouseEvent) {
    e.stopPropagation();
    if (!audioEl) return;
    if (playing) {
      audioEl.pause();
    } else {
      audioEl.currentTime = 0;
      audioEl.play().catch(() => {});
    }
  }
</script>

<div
  role="button"
  tabindex="0"
  class="text-left rounded-2xl border p-4 flex flex-col gap-2 transition-colors relative cursor-pointer
    {selected ? 'border-accent bg-accent-soft' : 'border-border bg-surface hover:border-accent/40'}"
  onclick={onSelect}
  onkeydown={(e) => e.key === "Enter" && onSelect()}
>
  <audio
    bind:this={audioEl}
    src={voice.preview_url}
    preload="none"
    onplay={() => (playing = true)}
    onpause={() => (playing = false)}
    onended={() => (playing = false)}
  ></audio>

  <div class="flex items-start justify-between gap-2">
    <div class="min-w-0">
      <div class="font-medium text-sm truncate">{voice.name}</div>
      <div class="text-xs text-muted mt-0.5 flex items-center gap-1.5">
        <span>{langFlag(voice.lang)}</span>
        {#if voice.gender}<span class="capitalize">{voice.gender}</span>{/if}
        {#if !voice.builtin}<span class="text-accent">· custom</span>{/if}
      </div>
    </div>
    <div class="flex items-center gap-1 shrink-0">
      {#if !voice.builtin && onDelete}
        <button
          class="w-7 h-7 rounded-full flex items-center justify-center text-muted hover:bg-danger-soft hover:text-danger transition-colors"
          onclick={(e) => { e.stopPropagation(); onDelete(); }}
          aria-label="Delete voice"
        >
          <Icon name="trash" size={13} />
        </button>
      {/if}
      <button
        class="w-9 h-9 rounded-full flex items-center justify-center bg-surface-2 hover:bg-accent hover:text-accent-fg transition-colors"
        onclick={togglePreview}
        aria-label={playing ? "Pause preview" : "Play preview"}
      >
        <Icon name={playing ? "pause" : "play"} size={14} />
      </button>
    </div>
  </div>
  {#if voice.description}
    <p class="text-xs text-muted line-clamp-2">{voice.description}</p>
  {/if}
</div>
