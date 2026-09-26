<script lang="ts">
  let {
    tone = "neutral",
    children,
  }: { tone?: "neutral" | "accent" | "success" | "warning" | "danger"; children: import("svelte").Snippet } = $props();

  const toneClasses: Record<string, string> = {
    neutral: "bg-surface-2 text-muted",
    accent: "bg-accent-soft text-accent",
    success: "bg-success-soft text-success",
    warning: "bg-warning-soft text-warning",
    danger: "bg-danger-soft text-danger",
  };
</script>

<span class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium {toneClasses[tone]}">
  {@render children()}
</span>
