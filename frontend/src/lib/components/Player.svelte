<script lang="ts">
  import { playerStore } from "../stores/player.svelte";
  import CoverThumb from "./CoverThumb.svelte";
  import Icon from "../icons/Icon.svelte";
  import { formatClock } from "../format";

  let audioEl: HTMLAudioElement;
  $effect(() => {
    if (audioEl) playerStore.attachAudio(audioEl);
  });

  const job = $derived(playerStore.job);
  const chapter = $derived(playerStore.chapter);

  function isTypingTarget(el: EventTarget | null): boolean {
    const tag = (el as HTMLElement | null)?.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || (el as HTMLElement | null)?.isContentEditable === true;
  }

  function onKeydown(e: KeyboardEvent) {
    if (!job || isTypingTarget(e.target)) return;
    if (e.code === "Space") {
      e.preventDefault();
      playerStore.toggle();
    } else if (e.key === "ArrowRight") {
      playerStore.seekBy(5);
    } else if (e.key === "ArrowLeft") {
      playerStore.seekBy(-5);
    }
  }

  const rates = [0.75, 1, 1.25, 1.5, 1.75, 2];
</script>

<svelte:window onkeydown={onKeydown} />

<audio bind:this={audioEl}></audio>

{#if job}
  <div class="h-20 shrink-0 border-t border-border bg-surface flex items-center gap-4 px-4 relative">
    <div class="flex items-center gap-3 w-56 min-w-0 shrink-0">
      <CoverThumb jobId={job.id} title={job.title} hasCover={job.has_cover} size={44} />
      <div class="min-w-0">
        <div class="text-sm font-medium truncate">{job.title}</div>
        <div class="text-xs text-muted truncate">{chapter?.title || "—"}</div>
      </div>
    </div>

    <div class="flex-1 min-w-0 flex flex-col items-center gap-1">
      <div class="flex items-center gap-3">
        <button class="text-muted hover:text-fg" onclick={() => playerStore.prevChapter()} aria-label="Previous chapter">
          <Icon name="skip-back" size={16} />
        </button>
        <button class="text-muted hover:text-fg" onclick={() => playerStore.seekBy(-15)} aria-label="Back 15 seconds">
          <Icon name="rewind-15" size={19} />
        </button>
        <button
          class="w-9 h-9 rounded-full bg-accent text-accent-fg flex items-center justify-center hover:bg-accent-hover transition-colors"
          onclick={() => playerStore.toggle()}
          aria-label={playerStore.isPlaying ? "Pause" : "Play"}
        >
          <Icon name={playerStore.isPlaying ? "pause" : "play"} size={16} />
        </button>
        <button class="text-muted hover:text-fg" onclick={() => playerStore.seekBy(30)} aria-label="Forward 30 seconds">
          <Icon name="forward-30" size={19} />
        </button>
        <button class="text-muted hover:text-fg" onclick={() => playerStore.nextChapter()} aria-label="Next chapter">
          <Icon name="skip-forward" size={16} />
        </button>
      </div>
      <div class="w-full max-w-xl flex items-center gap-2 text-xs text-muted tabular-nums">
        <span>{formatClock(playerStore.currentTime)}</span>
        {#if playerStore.mode === "segments"}
          <div class="flex-1 h-1.5 rounded-full bg-surface-2 overflow-hidden">
            <div class="h-full bg-accent/60 animate-pulse w-full"></div>
          </div>
          <span class="whitespace-nowrap">{playerStore.waiting ? "waiting for next segment…" : "generating…"}</span>
        {:else}
          <input
            type="range"
            min="0"
            max={playerStore.duration || 0}
            value={playerStore.currentTime}
            oninput={(e) => playerStore.seekTo(Number((e.target as HTMLInputElement).value))}
            class="flex-1 accent-[var(--color-accent)]"
          />
          <span>{formatClock(playerStore.duration)}</span>
        {/if}
      </div>
    </div>

    <div class="flex items-center gap-3 w-56 justify-end shrink-0">
      <select
        class="text-xs rounded-md border border-border bg-surface px-1.5 py-1"
        value={playerStore.rate}
        onchange={(e) => playerStore.setRate(Number((e.target as HTMLSelectElement).value))}
        aria-label="Playback speed"
      >
        {#each rates as r}<option value={r}>{r}×</option>{/each}
      </select>
      <div class="flex items-center gap-1.5">
        <Icon name="volume" size={15} class="text-muted" />
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          value={playerStore.volume}
          oninput={(e) => playerStore.setVolume(Number((e.target as HTMLInputElement).value))}
          class="w-16 accent-[var(--color-accent)]"
          aria-label="Volume"
        />
      </div>
      <button
        class="text-muted hover:text-fg relative"
        onclick={() => (playerStore.queueOpen = !playerStore.queueOpen)}
        aria-label="Queue"
      >
        <Icon name="queue" size={17} />
      </button>
    </div>

    {#if playerStore.queueOpen}
      <div class="absolute bottom-full right-4 mb-2 w-72 max-h-96 overflow-y-auto rounded-xl border border-border bg-surface shadow-lift">
        <div class="px-3 py-2 border-b border-border text-xs font-semibold uppercase tracking-wide text-muted">Chapters</div>
        {#each playerStore.queue as c (c.index)}
          <button
            class="w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-surface-2 disabled:opacity-40
              {c.index === playerStore.chapterIndex ? 'text-accent font-medium' : ''}"
            disabled={c.status === "pending" || c.status === "skipped"}
            onclick={() => playerStore.playChapter(job.id, c.index)}
          >
            {#if c.index === playerStore.chapterIndex && playerStore.isPlaying}
              <Icon name="pause" size={12} />
            {:else}
              <Icon name="play" size={12} />
            {/if}
            <span class="truncate flex-1">{c.title || `Chapter ${c.index + 1}`}</span>
            {#if c.status !== "done"}<span class="text-xs text-muted shrink-0">{c.status}</span>{/if}
          </button>
        {/each}
      </div>
    {/if}
  </div>
{/if}
