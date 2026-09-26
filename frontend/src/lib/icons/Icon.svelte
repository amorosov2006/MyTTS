<script lang="ts">
  // Small hand-built inline icon set so the app needs zero external icon
  // fonts/CDNs. All icons share a 24x24 stroke-based grid (Lucide-like).
  let { name, size = 20, class: className = "" }: { name: string; size?: number; class?: string } = $props();
</script>

<svg
  xmlns="http://www.w3.org/2000/svg"
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="2"
  stroke-linecap="round"
  stroke-linejoin="round"
  class={className}
  aria-hidden="true"
>
  {#if name === "play"}
    <polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none" />
  {:else if name === "pause"}
    <rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none" />
    <rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none" />
  {:else if name === "skip-back"}
    <polygon points="19 20 9 12 19 4 19 20" fill="currentColor" stroke="none" />
    <line x1="5" y1="4" x2="5" y2="20" />
  {:else if name === "skip-forward"}
    <polygon points="5 4 15 12 5 20 5 4" fill="currentColor" stroke="none" />
    <line x1="19" y1="4" x2="19" y2="20" />
  {:else if name === "rewind-15"}
    <path d="M3 12a9 9 0 1 0 3-6.7" />
    <polyline points="3 4 3 9 8 9" />
    <text x="8.4" y="15.3" font-size="8.5" fill="currentColor" stroke="none" font-family="inherit">15</text>
  {:else if name === "forward-30"}
    <path d="M21 12a9 9 0 1 1-3-6.7" />
    <polyline points="21 4 21 9 16 9" />
    <text x="6.4" y="15.3" font-size="8.5" fill="currentColor" stroke="none" font-family="inherit">30</text>
  {:else if name === "volume"}
    <polygon points="4 9 8 9 12 5 12 19 8 15 4 15 4 9" fill="currentColor" stroke="none" />
    <path d="M16 8a5 5 0 0 1 0 8" />
    <path d="M18.5 5.5a9 9 0 0 1 0 13" />
  {:else if name === "volume-mute"}
    <polygon points="4 9 8 9 12 5 12 19 8 15 4 15 4 9" fill="currentColor" stroke="none" />
    <line x1="16" y1="9" x2="22" y2="15" />
    <line x1="22" y1="9" x2="16" y2="15" />
  {:else if name === "queue"}
    <line x1="8" y1="6" x2="21" y2="6" />
    <line x1="8" y1="12" x2="21" y2="12" />
    <line x1="8" y1="18" x2="21" y2="18" />
    <circle cx="4" cy="6" r="1.4" fill="currentColor" />
    <circle cx="4" cy="12" r="1.4" fill="currentColor" />
    <circle cx="4" cy="18" r="1.4" fill="currentColor" />
  {:else if name === "sun"}
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  {:else if name === "moon"}
    <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" fill="currentColor" stroke="none" />
  {:else if name === "plus"}
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  {:else if name === "upload"}
    <path d="M12 16V4" />
    <polyline points="6 9 12 3 18 9" />
    <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
  {:else if name === "check"}
    <polyline points="20 6 9 17 4 12" />
  {:else if name === "check-circle"}
    <circle cx="12" cy="12" r="9" />
    <polyline points="8 12.5 11 15.5 16 9" />
  {:else if name === "x"}
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  {:else if name === "x-circle"}
    <circle cx="12" cy="12" r="9" />
    <line x1="9.5" y1="9.5" x2="14.5" y2="14.5" />
    <line x1="14.5" y1="9.5" x2="9.5" y2="14.5" />
  {:else if name === "chevron-down"}
    <polyline points="6 9 12 15 18 9" />
  {:else if name === "chevron-right"}
    <polyline points="9 6 15 12 9 18" />
  {:else if name === "chevron-left"}
    <polyline points="15 6 9 12 15 18" />
  {:else if name === "trash"}
    <polyline points="3 6 5 6 21 6" />
    <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
    <line x1="10" y1="11" x2="10" y2="17" />
    <line x1="14" y1="11" x2="14" y2="17" />
  {:else if name === "pencil"}
    <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
  {:else if name === "folder"}
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
  {:else if name === "mic"}
    <rect x="9" y="2" width="6" height="12" rx="3" />
    <path d="M5 10a7 7 0 0 0 14 0" />
    <line x1="12" y1="17" x2="12" y2="22" />
    <line x1="8" y1="22" x2="16" y2="22" />
  {:else if name === "wand"}
    <path d="m15 4 1.5 1.5M19 8l1.5 1.5M4 20l9-9M12 4l1 3 3 1-3 1-1 3-1-3-3-1 3-1Z" />
  {:else if name === "search"}
    <circle cx="11" cy="11" r="7" />
    <line x1="21" y1="21" x2="16.2" y2="16.2" />
  {:else if name === "alert-triangle"}
    <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
    <line x1="12" y1="9" x2="12" y2="13.5" />
    <circle cx="12" cy="16.8" r="0.9" fill="currentColor" stroke="none" />
  {:else if name === "info"}
    <circle cx="12" cy="12" r="9" />
    <line x1="12" y1="11" x2="12" y2="16.5" />
    <circle cx="12" cy="7.7" r="0.9" fill="currentColor" stroke="none" />
  {:else if name === "book"}
    <path d="M4 19.5V5a2 2 0 0 1 2-2h13v16.5" />
    <path d="M6 21.5a2 2 0 0 1 0-4h13v4Z" />
  {:else if name === "settings"}
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 1 1-4 0v-.2a1.7 1.7 0 0 0-1.1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.2a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.6V3a2 2 0 1 1 4 0v.2a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.6 1H21a2 2 0 1 1 0 4h-.2a1.7 1.7 0 0 0-1.6 1Z" />
  {:else if name === "loader"}
    <line x1="12" y1="2" x2="12" y2="6" />
    <line x1="12" y1="18" x2="12" y2="22" opacity="0.3" />
    <line x1="4.9" y1="4.9" x2="7.8" y2="7.8" opacity="0.85" />
    <line x1="16.2" y1="16.2" x2="19.1" y2="19.1" opacity="0.4" />
    <line x1="2" y1="12" x2="6" y2="12" opacity="0.7" />
    <line x1="18" y1="12" x2="22" y2="12" opacity="0.55" />
    <line x1="4.9" y1="19.1" x2="7.8" y2="16.2" opacity="0.25" />
    <line x1="16.2" y1="7.8" x2="19.1" y2="4.9" opacity="1" />
  {:else if name === "music"}
    <path d="M9 18V5l11-2v13" />
    <circle cx="6" cy="18" r="3" />
    <circle cx="17" cy="16" r="3" />
  {:else if name === "user"}
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5" />
  {:else if name === "sparkles"}
    <path d="M12 3v4M12 17v4M3 12h4M17 12h4" />
    <path d="M12 8a4 4 0 0 0 4 4 4 4 0 0 0-4 4 4 4 0 0 0-4-4 4 4 0 0 0 4-4Z" fill="currentColor" stroke="none" />
  {:else if name === "cover"}
    <rect x="4" y="3" width="16" height="18" rx="2" />
    <path d="M8 3v18" />
  {:else if name === "warning-fill"}
    <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" fill="currentColor" stroke="none" />
  {:else if name === "clock"}
    <circle cx="12" cy="12" r="9" />
    <polyline points="12 7 12 12 15.5 14" />
  {:else if name === "square"}
    <rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" stroke="none" />
  {/if}
</svg>
