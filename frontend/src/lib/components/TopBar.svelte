<script lang="ts">
  import Icon from "../icons/Icon.svelte";
  import { systemStore } from "../stores/system.svelte";
  import { themeStore } from "../stores/theme.svelte";

  const worker = $derived(systemStore.worker);
  const cap = $derived(systemStore.info?.memory.cap_gb ?? 30);
  const footprint = $derived(worker ? worker.footprint_gb + 0.6 : 0);

  const stateTone: Record<string, string> = {
    idle: "bg-success",
    busy: "bg-accent",
    starting: "bg-accent",
    restarting: "bg-warning",
    failed: "bg-danger",
    stopped: "bg-muted",
  };

  const stateLabel: Record<string, string> = {
    idle: "Idle",
    busy: "Working",
    starting: "Starting",
    restarting: "Restarting",
    failed: "Failed",
    stopped: "Stopped",
  };
</script>

<header class="h-14 shrink-0 border-b border-border bg-surface/95 backdrop-blur flex items-center justify-between px-4 gap-4">
  <div class="flex items-center gap-2 font-semibold">
    <div class="w-7 h-7 rounded-lg bg-accent text-accent-fg flex items-center justify-center">
      <Icon name="book" size={15} />
    </div>
    <span>MyTTS</span>
    <span class="text-muted font-normal text-sm hidden sm:inline">Audiobook Studio</span>
  </div>

  <div class="flex items-center gap-2">
    {#if worker}
      <div
        class="flex items-center gap-2 rounded-full border border-border bg-surface-2 px-3 py-1.5 text-xs"
        title={worker.message ?? undefined}
      >
        <span class="w-2 h-2 rounded-full {stateTone[worker.state] ?? 'bg-muted'}"></span>
        <span class="font-medium">{stateLabel[worker.state] ?? worker.state}</span>
        <span class="text-muted">·</span>
        <span class="text-muted tabular-nums">{footprint.toFixed(1)} / {cap} GB</span>
      </div>
    {/if}

    <button
      class="p-2 rounded-full text-muted hover:bg-surface-2 hover:text-fg transition-colors"
      onclick={() => themeStore.toggle()}
      aria-label="Toggle theme"
    >
      <Icon name={themeStore.current === "dark" ? "sun" : "moon"} size={17} />
    </button>
  </div>
</header>
