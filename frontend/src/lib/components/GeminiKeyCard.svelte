<script lang="ts">
  import * as api from "../api";
  import { enginesStore } from "../stores/engines.svelte";
  import Icon from "../icons/Icon.svelte";

  const gemini = $derived(enginesStore.gemini);

  let keyInput = $state("");
  let saving = $state(false);
  let error = $state("");

  async function save() {
    const key = keyInput.trim();
    if (!key) return;
    saving = true;
    error = "";
    try {
      const status = await api.putGeminiKey(key);
      enginesStore.setGeminiKeyStatus(status);
      keyInput = "";
    } catch (err: any) {
      error = err.detail || "Could not verify this key.";
    } finally {
      saving = false;
    }
  }

  async function remove() {
    if (!confirm("Remove the stored Gemini API key?")) return;
    error = "";
    try {
      await api.deleteGeminiKey();
      enginesStore.setGeminiKeyStatus({ configured: false, last4: null, source: null });
    } catch (err: any) {
      error = err.detail || "Could not remove the key.";
    }
  }
</script>

<div class="rounded-xl border border-border bg-surface p-4 space-y-3">
  {#if gemini?.key?.configured}
    <div class="flex items-center justify-between gap-3 flex-wrap">
      <div class="flex items-center gap-2 text-sm">
        <Icon name="check-circle" size={15} class="text-success shrink-0" />
        <span>Connected · key …{gemini.key.last4}</span>
      </div>
      <button class="text-sm text-danger hover:underline" onclick={remove}>Remove key</button>
    </div>
  {:else}
    <div class="text-sm font-medium flex items-center gap-1.5">
      <Icon name="key" size={15} /> Connect Google Gemini
    </div>
    <div class="flex flex-col sm:flex-row gap-2">
      <input
        type="password"
        class="flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm"
        placeholder="Gemini API key"
        autocomplete="off"
        bind:value={keyInput}
        onkeydown={(e) => e.key === "Enter" && save()}
      />
      <button
        class="rounded-lg bg-accent text-accent-fg px-3 py-2 text-sm font-medium hover:bg-accent-hover disabled:opacity-50 flex items-center justify-center gap-1.5 shrink-0"
        onclick={save}
        disabled={!keyInput.trim() || saving}
      >
        {#if saving}<Icon name="loader" size={14} class="animate-spin" />{/if} Save &amp; verify
      </button>
    </div>
    {#if error}
      <div class="text-xs text-danger">{error}</div>
    {/if}
    <p class="text-xs text-muted leading-relaxed">
      Get a key at <a class="text-accent hover:underline" href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">aistudio.google.com/apikey</a>.
      Gemini 3.8 models need billing enabled on the key's Google Cloud project; Gemini 2.5 Flash TTS (preview) has a free tier, but on the free tier Google may use your text to improve its products.
    </p>
  {/if}
</div>
