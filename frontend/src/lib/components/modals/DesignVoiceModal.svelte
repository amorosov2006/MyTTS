<script lang="ts">
  import Modal from "../ui/Modal.svelte";
  import Icon from "../../icons/Icon.svelte";
  import * as api from "../../api";
  import { toastStore } from "../../stores/toasts.svelte";
  import type { Lang } from "../../types";

  let { defaultLang, onClose }: { defaultLang: Lang; onClose: () => void } = $props();

  let name = $state("");
  let lang = $state<Lang>(defaultLang);
  let gender = $state<"male" | "female" | "">("");
  let description = $state("");
  let submitting = $state(false);

  const chips = [
    "calm deep male narrator, slow pace",
    "warm gentle female voice, mid pace",
    "energetic young narrator, upbeat",
    "measured documentary narrator, low pitch",
  ];

  async function submit() {
    if (!name.trim() || !description.trim()) return;
    submitting = true;
    try {
      await api.designVoice({ name: name.trim(), lang, description: description.trim(), gender: gender || undefined });
      toastStore.info("Designing your voice — it will appear in the gallery shortly.");
      onClose();
    } catch (err: any) {
      toastStore.error(err.detail || "Could not start voice design.");
    } finally {
      submitting = false;
    }
  }
</script>

<Modal title="Design a voice" {onClose}>
  <div class="space-y-4">
    <div>
      <label class="block text-sm font-medium mb-1" for="dv-name">Name</label>
      <input id="dv-name" class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm" bind:value={name} placeholder="e.g. Late-night storyteller" />
    </div>
    <div class="flex gap-3">
      <div class="flex-1">
        <label class="block text-sm font-medium mb-1" for="dv-lang">Language</label>
        <select id="dv-lang" class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm" bind:value={lang}>
          <option value="ru">Russian</option>
          <option value="en">English</option>
        </select>
      </div>
      <div class="flex-1">
        <label class="block text-sm font-medium mb-1" for="dv-gender">Gender</label>
        <select id="dv-gender" class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm" bind:value={gender}>
          <option value="">Any</option>
          <option value="male">Male</option>
          <option value="female">Female</option>
        </select>
      </div>
    </div>
    <div>
      <label class="block text-sm font-medium mb-1" for="dv-desc">Describe the voice</label>
      <textarea
        id="dv-desc"
        class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm resize-none"
        rows="3"
        bind:value={description}
        placeholder="calm deep male narrator, slow pace"
      ></textarea>
      <div class="flex flex-wrap gap-1.5 mt-2">
        {#each chips as c}
          <button
            type="button"
            class="text-xs rounded-full border border-border px-2.5 py-1 hover:border-accent hover:text-accent transition-colors"
            onclick={() => (description = c)}
          >
            {c}
          </button>
        {/each}
      </div>
    </div>
  </div>
  {#snippet footer()}
    <button class="px-4 py-2 rounded-lg text-sm text-muted hover:bg-surface-2" onclick={onClose}>Cancel</button>
    <button
      class="px-4 py-2 rounded-lg text-sm font-medium bg-accent text-accent-fg hover:bg-accent-hover disabled:opacity-50 flex items-center gap-2"
      disabled={!name.trim() || !description.trim() || submitting}
      onclick={submit}
    >
      {#if submitting}<Icon name="loader" size={14} class="animate-spin" />{/if}
      Design voice
    </button>
  {/snippet}
</Modal>
