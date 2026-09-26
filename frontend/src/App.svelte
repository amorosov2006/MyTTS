<script lang="ts">
  import { onMount } from "svelte";
  import TopBar from "./lib/components/TopBar.svelte";
  import Sidebar from "./lib/components/Sidebar.svelte";
  import StepTabs from "./lib/components/StepTabs.svelte";
  import Player from "./lib/components/Player.svelte";
  import Toasts from "./lib/components/Toasts.svelte";
  import BookStep from "./lib/components/steps/BookStep.svelte";
  import ChaptersStep from "./lib/components/steps/ChaptersStep.svelte";
  import VoiceStep from "./lib/components/steps/VoiceStep.svelte";
  import SampleStep from "./lib/components/steps/SampleStep.svelte";
  import ConvertStep from "./lib/components/steps/ConvertStep.svelte";
  import Icon from "./lib/icons/Icon.svelte";

  import { jobsStore } from "./lib/stores/jobs.svelte";
  import { voicesStore } from "./lib/stores/voices.svelte";
  import { systemStore } from "./lib/stores/system.svelte";
  import { uiStore } from "./lib/stores/ui.svelte";
  import { initSse } from "./lib/stores/sse.svelte";

  onMount(() => {
    jobsStore.refresh();
    voicesStore.refresh();
    systemStore.refresh();
    initSse();
  });

  const job = $derived(!uiStore.creatingNew ? jobsStore.selected : null);
  const step = $derived(job ? uiStore.stepFor(job.id) : "book");
</script>

<div class="h-screen flex flex-col overflow-hidden">
  <TopBar />
  <div class="flex-1 flex min-h-0">
    <Sidebar />
    <main class="flex-1 min-w-0 flex flex-col bg-bg">
      {#if uiStore.creatingNew}
        <div class="flex-1 overflow-y-auto">
          <BookStep />
        </div>
      {:else if job}
        <StepTabs active={step} sampleApproved={job.sample_approved} onSelect={(s) => uiStore.setStep(job.id, s)} />
        <div class="flex-1 min-h-0 overflow-y-auto">
          {#if step === "book"}
            <BookStep {job} />
          {:else if step === "chapters"}
            <ChaptersStep {job} />
          {:else if step === "voice"}
            <VoiceStep {job} />
          {:else if step === "sample"}
            <SampleStep {job} />
          {:else if step === "convert"}
            <ConvertStep {job} />
          {/if}
        </div>
      {:else}
        <div class="flex-1 flex flex-col items-center justify-center text-center gap-3 text-muted p-8">
          <Icon name="book" size={40} class="opacity-40" />
          <div class="text-lg font-medium text-fg">No book selected</div>
          <p class="text-sm max-w-sm">Choose a book from the library, or add a new one to start turning it into an audiobook.</p>
          <button
            class="mt-2 inline-flex items-center gap-2 rounded-xl bg-accent text-accent-fg font-medium px-4 py-2.5 hover:bg-accent-hover transition-colors"
            onclick={() => uiStore.startNewBook()}
          >
            <Icon name="plus" size={16} /> New book
          </button>
        </div>
      {/if}
    </main>
  </div>
  <Player />
  <Toasts />
</div>
