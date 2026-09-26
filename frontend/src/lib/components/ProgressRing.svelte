<script lang="ts">
  let { fraction = 0, size = 22, stroke = 3 }: { fraction?: number; size?: number; stroke?: number } = $props();
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  let dash = $derived(c * Math.min(1, Math.max(0, fraction)));
</script>

<svg width={size} height={size} viewBox="0 0 {size} {size}" class="shrink-0 -rotate-90">
  <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-border)" stroke-width={stroke} />
  <circle
    cx={size / 2}
    cy={size / 2}
    r={r}
    fill="none"
    stroke="var(--color-accent)"
    stroke-width={stroke}
    stroke-linecap="round"
    stroke-dasharray="{dash} {c}"
  />
</svg>
