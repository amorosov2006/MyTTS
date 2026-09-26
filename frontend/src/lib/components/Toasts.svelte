<script lang="ts">
  import { toastStore } from "../stores/toasts.svelte";
  import Icon from "../icons/Icon.svelte";

  const iconFor = { info: "info", success: "check-circle", warning: "alert-triangle", error: "x-circle" } as const;
  const toneFor = {
    info: "border-border bg-surface text-fg",
    success: "border-success/30 bg-success-soft text-success",
    warning: "border-warning/30 bg-warning-soft text-warning",
    error: "border-danger/30 bg-danger-soft text-danger",
  } as const;
</script>

<div class="fixed bottom-24 right-4 z-[60] flex flex-col gap-2 w-80 max-w-[calc(100vw-2rem)]">
  {#each toastStore.items as t (t.id)}
    <div class="flex items-start gap-2 rounded-xl border px-3 py-2.5 shadow-lift text-sm {toneFor[t.level]}">
      <Icon name={iconFor[t.level]} size={16} class="mt-0.5 shrink-0" />
      <div class="flex-1">{t.message}</div>
      <button class="text-muted hover:text-fg" onclick={() => toastStore.dismiss(t.id)} aria-label="Dismiss">
        <Icon name="x" size={14} />
      </button>
    </div>
  {/each}
</div>
