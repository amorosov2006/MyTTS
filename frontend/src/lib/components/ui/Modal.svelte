<script lang="ts">
  import Icon from "../../icons/Icon.svelte";

  let {
    title,
    onClose,
    children,
    footer,
    wide = false,
  }: {
    title: string;
    onClose: () => void;
    children: import("svelte").Snippet;
    footer?: import("svelte").Snippet;
    wide?: boolean;
  } = $props();

  function onKeydown(e: KeyboardEvent) {
    if (e.key === "Escape") onClose();
  }
</script>

<svelte:window onkeydown={onKeydown} />

<div
  class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-[2px]"
  role="presentation"
  onclick={onClose}
>
  <div
    class="w-full {wide ? 'max-w-2xl' : 'max-w-md'} max-h-[85vh] overflow-y-auto rounded-2xl bg-surface shadow-lift border border-border"
    role="document"
    onclick={(e) => e.stopPropagation()}
  >
    <div class="flex items-center justify-between px-5 py-4 border-b border-border sticky top-0 bg-surface">
      <h2 class="text-base font-semibold">{title}</h2>
      <button
        class="p-1.5 rounded-lg text-muted hover:bg-surface-2 hover:text-fg transition-colors"
        onclick={onClose}
        aria-label="Close"
      >
        <Icon name="x" size={18} />
      </button>
    </div>
    <div class="px-5 py-4">
      {@render children()}
    </div>
    {#if footer}
      <div class="px-5 py-4 border-t border-border flex justify-end gap-2">
        {@render footer()}
      </div>
    {/if}
  </div>
</div>
