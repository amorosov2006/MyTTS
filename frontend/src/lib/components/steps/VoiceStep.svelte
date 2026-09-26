<script lang="ts">
  import type { JobInfo, JobSettings } from "../../types";
  import { voicesStore } from "../../stores/voices.svelte";
  import { jobsStore } from "../../stores/jobs.svelte";
  import { toastStore } from "../../stores/toasts.svelte";
  import * as api from "../../api";
  import { debounce } from "../../debounce";
  import VoiceCard from "../VoiceCard.svelte";
  import Icon from "../../icons/Icon.svelte";
  import DesignVoiceModal from "../modals/DesignVoiceModal.svelte";
  import CloneVoiceModal from "../modals/CloneVoiceModal.svelte";

  let { job }: { job: JobInfo } = $props();

  const editable = $derived(job.status === "parsed" || job.status === "paused");

  let settings = $state<JobSettings>($state.snapshot(job.settings));
  let lastJobId = job.id;
  $effect(() => {
    if (job.id !== lastJobId) {
      lastJobId = job.id;
      settings = $state.snapshot(job.settings);
    }
  });

  let showAllVoices = $state(false);
  let showDesign = $state(false);
  let showClone = $state(false);
  let pickingFolder = $state(false);
  let saveState = $state<"idle" | "saving" | "saved">("idle");

  const bookLang = $derived(job.lang ?? "en");
  const visibleVoices = $derived(showAllVoices ? voicesStore.items : voicesStore.items.filter((v) => v.lang === bookLang));

  const save = debounce(async (next: JobSettings) => {
    saveState = "saving";
    try {
      const updated = await api.putSettings(job.id, next);
      jobsStore.upsert(updated);
      saveState = "saved";
      setTimeout(() => (saveState = "idle"), 1500);
    } catch (err: any) {
      toastStore.error(err.detail || "Could not save settings.");
      saveState = "idle";
    }
  }, 500);

  function update<K extends keyof JobSettings>(key: K, value: JobSettings[K]) {
    settings = { ...settings, [key]: value };
    save(settings);
  }

  function updateParam(key: "temperature", value: number) {
    settings = { ...settings, params: { ...settings.params, [key]: value } };
    save(settings);
  }

  async function chooseFolder() {
    pickingFolder = true;
    try {
      const res = await api.pickFolder(settings.output_dir);
      if (res.path) update("output_dir", res.path);
    } catch (err: any) {
      toastStore.error(err.detail || "Could not open the folder picker.");
    } finally {
      pickingFolder = false;
    }
  }

  async function removeVoice(id: string) {
    if (!confirm("Delete this voice?")) return;
    try {
      await api.deleteVoice(id);
      voicesStore.remove(id);
      if (settings.voice_id === id) update("voice_id", bookLang === "ru" ? "ru_male" : "en_male");
    } catch (err: any) {
      toastStore.error(err.detail || "Could not delete this voice.");
    }
  }
</script>

<div class="max-w-4xl mx-auto p-6 space-y-8">
  <fieldset disabled={!editable} class="space-y-8" class:opacity-60={!editable}>
    <section>
      <div class="flex items-center justify-between flex-wrap gap-2 mb-3">
        <h2 class="text-lg font-semibold">Voice</h2>
        <div class="flex items-center gap-3 text-sm">
          <label class="flex items-center gap-1.5 text-muted">
            <input type="checkbox" bind:checked={showAllVoices} class="accent-[var(--color-accent)]" />
            Show all languages
          </label>
          <button class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 hover:border-accent hover:text-accent" onclick={() => (showDesign = true)}>
            <Icon name="wand" size={14} /> Design a voice
          </button>
          <button class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 hover:border-accent hover:text-accent" onclick={() => (showClone = true)}>
            <Icon name="mic" size={14} /> Clone from a recording
          </button>
        </div>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {#each visibleVoices as v (v.id)}
          <VoiceCard voice={v} selected={settings.voice_id === v.id} onSelect={() => update("voice_id", v.id)} onDelete={() => removeVoice(v.id)} />
        {/each}
      </div>
    </section>

    <section class="grid sm:grid-cols-2 gap-6">
      <div>
        <div class="flex justify-between text-sm mb-1">
          <span class="font-medium">Speed</span>
          <span class="text-muted tabular-nums">{settings.speed.toFixed(2)}×</span>
        </div>
        <input
          type="range"
          min="0.7"
          max="1.5"
          step="0.05"
          value={settings.speed}
          oninput={(e) => update("speed", Number((e.target as HTMLInputElement).value))}
          class="w-full accent-[var(--color-accent)]"
        />
      </div>
      <div>
        <div class="flex justify-between text-sm mb-1">
          <span class="font-medium">Expressiveness</span>
          <span class="text-muted">{settings.params.temperature < 0.7 ? "Calm" : settings.params.temperature > 0.85 ? "Lively" : "Balanced"}</span>
        </div>
        <input
          type="range"
          min="0.5"
          max="1.0"
          step="0.05"
          value={settings.params.temperature}
          oninput={(e) => updateParam("temperature", Number((e.target as HTMLInputElement).value))}
          class="w-full accent-[var(--color-accent)]"
        />
        <div class="flex justify-between text-xs text-muted mt-0.5"><span>Calm</span><span>Lively</span></div>
      </div>
      <div>
        <div class="flex justify-between text-sm mb-1">
          <span class="font-medium">Paragraph pause</span>
          <span class="text-muted tabular-nums">{settings.pause_paragraph_ms} ms</span>
        </div>
        <input
          type="range"
          min="0"
          max="2000"
          step="50"
          value={settings.pause_paragraph_ms}
          oninput={(e) => update("pause_paragraph_ms", Number((e.target as HTMLInputElement).value))}
          class="w-full accent-[var(--color-accent)]"
        />
      </div>
      <div>
        <div class="flex justify-between text-sm mb-1">
          <span class="font-medium">Sentence pause</span>
          <span class="text-muted tabular-nums">{settings.pause_sentence_ms} ms</span>
        </div>
        <input
          type="range"
          min="0"
          max="1000"
          step="25"
          value={settings.pause_sentence_ms}
          oninput={(e) => update("pause_sentence_ms", Number((e.target as HTMLInputElement).value))}
          class="w-full accent-[var(--color-accent)]"
        />
      </div>
    </section>

    <section class="grid sm:grid-cols-2 gap-3">
      <label class="flex items-center justify-between rounded-xl border border-border bg-surface px-4 py-3 text-sm cursor-pointer">
        <span>Read chapter titles</span>
        <input type="checkbox" checked={settings.read_titles} onchange={(e) => update("read_titles", (e.target as HTMLInputElement).checked)} class="accent-[var(--color-accent)]" />
      </label>
      <label class="flex items-center justify-between rounded-xl border border-border bg-surface px-4 py-3 text-sm cursor-pointer">
        <span>Skip footnotes</span>
        <input type="checkbox" checked={settings.skip_footnotes} onchange={(e) => update("skip_footnotes", (e.target as HTMLInputElement).checked)} class="accent-[var(--color-accent)]" />
      </label>
      <label class="flex items-center justify-between rounded-xl border border-border bg-surface px-4 py-3 text-sm cursor-pointer sm:col-span-2">
        <span>
          Quality check (Whisper QA)
          <span class="block text-xs text-muted font-normal mt-0.5">Slower, but catches skipped or garbled phrases and regenerates them.</span>
        </span>
        <input type="checkbox" checked={settings.qa} onchange={(e) => update("qa", (e.target as HTMLInputElement).checked)} class="accent-[var(--color-accent)] shrink-0 ml-3" />
      </label>
    </section>

    <section class="grid sm:grid-cols-2 gap-4">
      <div>
        <label class="block text-sm font-medium mb-1" for="fmt">Output format</label>
        <select id="fmt" class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm" value={settings.output_format} onchange={(e) => update("output_format", (e.target as HTMLSelectElement).value as JobSettings["output_format"])}>
          <option value="mp3">MP3 per chapter</option>
          <option value="m4b">M4B audiobook</option>
          <option value="both">Both</option>
        </select>
      </div>
      <div>
        <label class="block text-sm font-medium mb-1" for="bitrate">Bitrate</label>
        <select id="bitrate" class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm" value={settings.bitrate} onchange={(e) => update("bitrate", (e.target as HTMLSelectElement).value)}>
          <option value="64k">64 kbps</option>
          <option value="96k">96 kbps</option>
          <option value="128k">128 kbps</option>
          <option value="192k">192 kbps</option>
        </select>
      </div>
      <div class="sm:col-span-2">
        <label class="block text-sm font-medium mb-1" for="outdir">Output folder</label>
        <div class="flex gap-2">
          <input id="outdir" class="flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm" value={settings.output_dir} oninput={(e) => update("output_dir", (e.target as HTMLInputElement).value)} />
          <button class="rounded-lg border border-border px-3 py-2 text-sm hover:border-accent hover:text-accent shrink-0 flex items-center gap-1.5" onclick={chooseFolder} disabled={pickingFolder}>
            <Icon name="folder" size={14} /> Choose…
          </button>
        </div>
      </div>
    </section>
  </fieldset>

  <div class="text-xs text-muted h-4">
    {#if saveState === "saving"}Saving…{:else if saveState === "saved"}Saved{/if}
  </div>
</div>

{#if showDesign}
  <DesignVoiceModal defaultLang={bookLang} onClose={() => (showDesign = false)} />
{/if}
{#if showClone}
  <CloneVoiceModal defaultLang={bookLang} onClose={() => (showClone = false)} />
{/if}
