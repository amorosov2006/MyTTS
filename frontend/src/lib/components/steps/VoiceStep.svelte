<script lang="ts">
  import type { EngineName, JobInfo, JobSettings, Lang } from "../../types";
  import { voicesStore } from "../../stores/voices.svelte";
  import { jobsStore } from "../../stores/jobs.svelte";
  import { toastStore } from "../../stores/toasts.svelte";
  import { systemStore } from "../../stores/system.svelte";
  import { enginesStore } from "../../stores/engines.svelte";
  import * as api from "../../api";
  import { debounce } from "../../debounce";
  import VoiceCard from "../VoiceCard.svelte";
  import GeminiVoiceCard from "../GeminiVoiceCard.svelte";
  import GeminiKeyCard from "../GeminiKeyCard.svelte";
  import Icon from "../../icons/Icon.svelte";
  import DesignVoiceModal from "../modals/DesignVoiceModal.svelte";
  import CloneVoiceModal from "../modals/CloneVoiceModal.svelte";
  import { bookFolderName, joinPath, estSecondsFromChars, formatHours } from "../../format";
  import { previewCacheKey, getCachedPreview, setCachedPreview } from "../../stores/geminiPreviewCache";

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
  const isGemini = $derived(settings.engine === "gemini");
  const visibleVoices = $derived(showAllVoices ? voicesStore.items : voicesStore.items.filter((v) => v.lang === bookLang));

  const outputDirDefault = $derived(systemStore.info?.output_dir_default ?? "");
  const effectiveOutputDir = $derived(settings.output_dir.trim() || outputDirDefault);
  const bookFolder = $derived(bookFolderName(job.author, job.title));
  const outputPreview = $derived(effectiveOutputDir ? `${joinPath(effectiveOutputDir, bookFolder)}/` : "");

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

  function setEngine(engine: EngineName) {
    if (!editable || settings.engine === engine) return;
    update("engine", engine);
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

  // ------------------------------------------------------------------- Gemini

  const gemini = $derived(enginesStore.gemini);
  const geminiConnected = $derived(!!gemini?.available);
  const effectiveGeminiModel = $derived(settings.gemini_model || gemini?.default_model || "");
  const selectedModelInfo = $derived(gemini?.models?.find((m) => m.id === effectiveGeminiModel));
  const effectiveGeminiVoice = $derived(settings.gemini_voice || gemini?.default_voice?.[bookLang] || "Charon");
  const effectiveGeminiStyle = $derived(settings.gemini_style);

  let genderFilter = $state<"all" | "female" | "male">("all");
  const visibleGeminiVoices = $derived(
    (gemini?.voices ?? []).filter((v) => genderFilter === "all" || v.gender === genderFilter)
  );

  let previewingVoiceId = $state<string | null>(null);
  let playingVoiceId = $state<string | null>(null);
  let previewAudio: HTMLAudioElement | null = $state(null);

  async function previewGeminiVoiceClick(voiceId: string) {
    if (previewingVoiceId) return;
    const lang: Lang = (settings.lang ?? job.lang ?? "ru") as Lang;
    const model = effectiveGeminiModel;
    const style = effectiveGeminiStyle;
    const key = previewCacheKey(voiceId, style, model);
    previewingVoiceId = voiceId;
    try {
      let url = getCachedPreview(key);
      if (!url) {
        const blob = await api.previewGeminiVoice({ voice: voiceId, lang, style: style || undefined, model: model || undefined });
        url = URL.createObjectURL(blob);
        setCachedPreview(key, url);
      }
      if (previewAudio) {
        previewAudio.src = url;
        playingVoiceId = voiceId;
        await previewAudio.play();
      }
    } catch (err: any) {
      toastStore.error(err.detail || "Could not preview this voice.");
    } finally {
      previewingVoiceId = null;
    }
  }

  const RU_STYLE_PRESETS = [
    "Спокойное тёплое чтение аудиокниги",
    "Живо и выразительно, с интонациями персонажей",
    "Медленно, мягко, как сказку на ночь",
    "Сдержанно и нейтрально, как документальный диктор",
  ];
  const EN_STYLE_PRESETS = [
    "Calm, warm audiobook narration",
    "Lively and expressive, with character voices",
    "Slow and gentle, like a bedtime story",
    "Reserved and neutral, like a documentary narrator",
  ];
  const stylePresets = $derived(bookLang === "ru" ? RU_STYLE_PRESETS : EN_STYLE_PRESETS);

  // Cost estimate: audio seconds ≈ chars / 14; tokens = seconds × audio_tokens_per_second;
  // cost ≈ tokens / 1e6 × usd_per_m_audio_tokens (docs/API.md "Estimating the cost").
  const includedChars = $derived(job.chapters.filter((c) => c.include).reduce((a, c) => a + c.chars, 0));
  const estAudioSeconds = $derived(estSecondsFromChars(includedChars));
  const audioTokensPerSecond = $derived(gemini?.audio_tokens_per_second ?? 25);
  const estUsd = $derived(selectedModelInfo ? ((estAudioSeconds * audioTokensPerSecond) / 1e6) * selectedModelInfo.usd_per_m_audio_tokens : 0);
</script>

<div class="max-w-4xl mx-auto p-6 space-y-8">
  {#if job.status === "done" || job.status === "cancelled"}
    <div class="rounded-xl border border-border bg-surface px-4 py-3 text-sm text-muted">
      This book is finished, so its settings are locked. Use <strong>Convert again</strong> on the Convert step to render it with another voice or style.
    </div>
  {:else if !editable}
    <div class="rounded-xl border border-border bg-surface px-4 py-3 text-sm text-muted">
      Settings are locked while converting — pause the job to change them.
    </div>
  {/if}
  <fieldset disabled={!editable} class="space-y-8" class:opacity-60={!editable}>
    <section>
      <h2 class="text-lg font-semibold mb-3">Engine</h2>
      <div class="inline-flex items-center gap-1 rounded-xl border border-border bg-surface-2 p-1 flex-wrap">
        <button
          type="button"
          class="px-4 py-2 rounded-lg text-sm font-medium transition-colors {!isGemini ? 'bg-surface shadow-sm text-fg' : 'text-muted hover:text-fg'}"
          onclick={() => setEngine("local")}
        >
          On this Mac · Qwen3 (offline, free)
        </button>
        <button
          type="button"
          class="px-4 py-2 rounded-lg text-sm font-medium transition-colors {isGemini ? 'bg-surface shadow-sm text-fg' : 'text-muted hover:text-fg'}"
          onclick={() => setEngine("gemini")}
        >
          Google Gemini (cloud)
        </button>
      </div>
    </section>

    {#if isGemini}
      <section class="space-y-4">
        <div class="rounded-xl border border-warning/40 bg-warning-soft px-4 py-3 text-sm text-fg flex items-start gap-2">
          <Icon name="cloud" size={16} class="shrink-0 mt-0.5 text-warning" />
          <span>The book text is sent to Google for speech synthesis. The local engine keeps everything on this Mac.</span>
        </div>

        <GeminiKeyCard />

        {#if geminiConnected}
          <div>
            <label class="block text-sm font-medium mb-1" for="gmodel">Model</label>
            <select
              id="gmodel"
              class="w-full sm:w-auto rounded-lg border border-border bg-surface px-3 py-2 text-sm"
              value={effectiveGeminiModel}
              onchange={(e) => update("gemini_model", (e.target as HTMLSelectElement).value)}
            >
              {#each gemini?.models ?? [] as m (m.id)}
                <option value={m.id}>{m.label}{m.free_tier ? " · free tier" : ""}</option>
              {/each}
            </select>
          </div>

          <div>
            <div class="flex items-center justify-between flex-wrap gap-2 mb-3">
              <h3 class="text-sm font-semibold text-muted uppercase tracking-wide">Voice</h3>
              <div class="flex items-center gap-1 rounded-lg border border-border p-0.5 text-xs">
                {#each [["all", "All"], ["female", "Female"], ["male", "Male"]] as [val, label] (val)}
                  <button
                    type="button"
                    class="px-2.5 py-1 rounded-md transition-colors {genderFilter === val ? 'bg-accent text-accent-fg' : 'text-muted hover:text-fg'}"
                    onclick={() => (genderFilter = val as typeof genderFilter)}
                  >{label}</button>
                {/each}
              </div>
            </div>
            <audio bind:this={previewAudio} onended={() => (playingVoiceId = null)} onpause={() => (playingVoiceId = null)}></audio>
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {#each visibleGeminiVoices as v (v.id)}
                <GeminiVoiceCard
                  voice={v}
                  selected={effectiveGeminiVoice === v.id}
                  previewing={previewingVoiceId === v.id}
                  playing={playingVoiceId === v.id}
                  onSelect={() => update("gemini_voice", v.id)}
                  onPreview={() => previewGeminiVoiceClick(v.id)}
                />
              {/each}
            </div>
          </div>

          <div>
            <label class="block text-sm font-medium mb-1" for="gstyle">Style instruction</label>
            <textarea
              id="gstyle"
              class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm resize-none"
              rows="2"
              value={settings.gemini_style}
              placeholder={gemini?.default_style?.[bookLang] ?? ""}
              oninput={(e) => update("gemini_style", (e.target as HTMLTextAreaElement).value)}
            ></textarea>
            <div class="flex flex-wrap gap-1.5 mt-2">
              {#each stylePresets as preset}
                <button
                  type="button"
                  class="rounded-full border border-border px-2.5 py-1 text-xs text-muted hover:border-accent hover:text-accent transition-colors"
                  onclick={() => update("gemini_style", preset)}
                >{preset}</button>
              {/each}
            </div>
          </div>

          {#if includedChars > 0}
            <div class="rounded-xl border border-border bg-surface-2 px-4 py-3 text-sm">
              {#if selectedModelInfo?.free_tier}
                <span class="font-medium">Free tier: $0</span> within Google's daily limits — otherwise
                ≈ <span class="font-medium tabular-nums">${estUsd.toFixed(2)}</span> for this book ({formatHours(estAudioSeconds)} of audio) at current Gemini pricing.
              {:else}
                ≈ <span class="font-medium tabular-nums">${estUsd.toFixed(2)}</span> for this book ({formatHours(estAudioSeconds)} of audio) at current Gemini pricing.
              {/if}
            </div>
          {/if}
        {/if}
      </section>
    {:else}
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
        {#if visibleVoices.length === 0}
          <div class="rounded-xl border border-dashed border-border px-4 py-8 text-center text-sm text-muted">
            No voices for this language yet.
            {#if !showAllVoices}
              <button class="text-accent hover:underline" onclick={() => (showAllVoices = true)}>Show all languages</button>,
            {/if}
            or design / clone one above.
          </div>
        {:else}
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {#each visibleVoices as v (v.id)}
              <VoiceCard voice={v} selected={settings.voice_id === v.id} onSelect={() => update("voice_id", v.id)} onDelete={() => removeVoice(v.id)} />
            {/each}
          </div>
        {/if}
      </section>
    {/if}

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
      {#if !isGemini}
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
      {/if}
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
      {#if isGemini}
        <div class="rounded-xl border border-border bg-surface px-4 py-3 text-sm text-muted sm:col-span-2">
          Gemini uses a length sanity check instead of Whisper QA.
        </div>
      {:else}
        <label class="flex items-center justify-between rounded-xl border border-border bg-surface px-4 py-3 text-sm cursor-pointer sm:col-span-2">
          <span>
            Quality check (Whisper QA)
            <span class="block text-xs text-muted font-normal mt-0.5">Slower, but catches skipped or garbled phrases and regenerates them.</span>
          </span>
          <input type="checkbox" checked={settings.qa} onchange={(e) => update("qa", (e.target as HTMLInputElement).checked)} class="accent-[var(--color-accent)] shrink-0 ml-3" />
        </label>
      {/if}
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
        <div class="flex items-center justify-between mb-1">
          <label class="block text-sm font-medium" for="outdir">Output folder</label>
          {#if !settings.output_dir.trim() && outputDirDefault}
            <span class="text-xs text-muted">Default</span>
          {/if}
        </div>
        <div class="flex gap-2">
          <input
            id="outdir"
            class="flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm"
            value={settings.output_dir}
            placeholder={outputDirDefault || undefined}
            oninput={(e) => update("output_dir", (e.target as HTMLInputElement).value)}
          />
          <button class="rounded-lg border border-border px-3 py-2 text-sm hover:border-accent hover:text-accent shrink-0 flex items-center gap-1.5" onclick={chooseFolder} disabled={pickingFolder}>
            <Icon name="folder" size={14} /> Choose…
          </button>
        </div>
        {#if outputPreview}
          <div class="mt-1.5 text-xs text-muted flex items-center gap-1.5 min-w-0">
            <Icon name="folder" size={12} class="shrink-0" />
            <span class="truncate font-mono" title={outputPreview}>{outputPreview}</span>
          </div>
        {/if}
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
