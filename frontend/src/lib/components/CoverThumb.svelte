<script lang="ts">
  import { jobCoverUrl } from "../api";
  import { gradientFor, initials } from "../format";

  let {
    jobId,
    title,
    hasCover = false,
    size = 48,
    rounded = "rounded-lg",
  }: { jobId: string; title: string; hasCover?: boolean; size?: number; rounded?: string } = $props();

  let broken = $state(false);
  const [c1, c2] = gradientFor(jobId || title);
</script>

{#if hasCover && !broken}
  <img
    src={jobCoverUrl(jobId)}
    alt=""
    class="{rounded} object-cover shrink-0"
    style="width:{size}px;height:{size}px"
    onerror={() => (broken = true)}
  />
{:else}
  <div
    class="{rounded} shrink-0 flex items-center justify-center font-semibold text-white/95 select-none"
    style="width:{size}px;height:{size}px;background:linear-gradient(155deg,{c1},{c2});font-size:{Math.max(10, size * 0.32)}px"
  >
    {initials(title)}
  </div>
{/if}
