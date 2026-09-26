<script lang="ts">
  import Modal from "../ui/Modal.svelte";
  import Icon from "../../icons/Icon.svelte";
  import * as api from "../../api";
  import { voicesStore } from "../../stores/voices.svelte";
  import { toastStore } from "../../stores/toasts.svelte";
  import type { Lang } from "../../types";

  let { defaultLang, onClose }: { defaultLang: Lang; onClose: () => void } = $props();

  let name = $state("");
  let lang = $state<Lang>(defaultLang);
  let gender = $state<"male" | "female" | "">("");
  let transcript = $state("");
  let file = $state<File | null>(null);
  let submitting = $state(false);

  function onFile(e: Event) {
    file = (e.target as HTMLInputElement).files?.[0] ?? null;
  }

  async function submit() {
    if (!file || !name.trim() || !transcript.trim()) return;
    submitting = true;
    try {
      const voice = await api.cloneVoice({ audio: file, transcript: transcript.trim(), name: name.trim(), lang, gender: gender || undefined });
      voicesStore.upsert(voice);
      toastStore.success(`Cloned voice "${voice.name}" is ready.`);
      onClose();
    } catch (err: any) {
      toastStore.error(err.detail || "Could not clone this voice.");
    } finally {
      submitting = false;
    }
  }
</script>

<Modal title="Clone from a recording" {onClose}>
  <div class="space-y-4">
    <div>
      <label class="block text-sm font-medium mb-1" for="cv-file">Reference recording (5–30 s, wav/mp3/m4a)</label>
      <input id="cv-file" type="file" accept="audio/*" class="w-full text-sm" onchange={onFile} />
    </div>
    <div>
      <label class="block text-sm font-medium mb-1" for="cv-transcript">Exact transcript of the recording</label>
      <textarea
        id="cv-transcript"
        class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm resize-none"
        rows="3"
        bind:value={transcript}
        placeholder="Type exactly what is said in the clip…"
      ></textarea>
    </div>
    <div>
      <label class="block text-sm font-medium mb-1" for="cv-name">Name</label>
      <input id="cv-name" class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm" bind:value={name} placeholder="e.g. Grandpa's voice" />
    </div>
    <div class="flex gap-3">
      <div class="flex-1">
        <label class="block text-sm font-medium mb-1" for="cv-lang">Language</label>
        <select id="cv-lang" class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm" bind:value={lang}>
          <option value="ru">Russian</option>
          <option value="en">English</option>
        </select>
      </div>
      <div class="flex-1">
        <label class="block text-sm font-medium mb-1" for="cv-gender">Gender</label>
        <select id="cv-gender" class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm" bind:value={gender}>
          <option value="">Any</option>
          <option value="male">Male</option>
          <option value="female">Female</option>
        </select>
      </div>
    </div>
  </div>
  {#snippet footer()}
    <button class="px-4 py-2 rounded-lg text-sm text-muted hover:bg-surface-2" onclick={onClose}>Cancel</button>
    <button
      class="px-4 py-2 rounded-lg text-sm font-medium bg-accent text-accent-fg hover:bg-accent-hover disabled:opacity-50 flex items-center gap-2"
      disabled={!file || !name.trim() || !transcript.trim() || submitting}
      onclick={submit}
    >
      {#if submitting}<Icon name="loader" size={14} class="animate-spin" />{/if}
      Clone voice
    </button>
  {/snippet}
</Modal>
