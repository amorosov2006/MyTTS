import { jobsStore } from "./jobs.svelte";
import { samplesStore } from "./samples.svelte";
import { voicesStore } from "./voices.svelte";
import { systemStore } from "./system.svelte";
import { toastStore } from "./toasts.svelte";
import { segmentsStore } from "./segments.svelte";
import { playerStore } from "./player.svelte";
import type { SseEvent } from "../types";

let es: EventSource | null = null;
let backoffMs = 1000;
const MAX_BACKOFF = 15000;

function handle(envelope: SseEvent) {
  switch (envelope.type) {
    case "job":
      jobsStore.upsert(envelope.data);
      break;
    case "segment":
      segmentsStore.append(envelope.job_id, envelope.data);
      playerStore.onSegmentEvent(envelope.job_id, envelope.data);
      break;
    case "chapter":
      jobsStore.applyChapter(envelope.job_id, envelope.data);
      playerStore.onChapterEvent(envelope.job_id, envelope.data);
      break;
    case "sample":
      samplesStore.upsert(envelope.data);
      break;
    case "voice":
      if (envelope.data.status === "ready") {
        voicesStore.upsert(envelope.data.voice);
        toastStore.success(`Voice "${envelope.data.voice.name}" is ready.`);
      } else {
        toastStore.error(envelope.data.error || "Voice design failed.");
      }
      break;
    case "worker":
      systemStore.applyWorker(envelope.data);
      break;
    case "log":
      if (envelope.data.level === "warning") toastStore.warning(envelope.data.message);
      else if (envelope.data.level === "error") toastStore.error(envelope.data.message);
      break;
  }
}

function connect() {
  es = new EventSource("/api/events");
  for (const type of ["job", "segment", "chapter", "sample", "voice", "worker", "log"] as const) {
    es.addEventListener(type, (e: MessageEvent) => {
      try {
        handle(JSON.parse(e.data));
      } catch {
        /* malformed frame, ignore */
      }
    });
  }
  es.onopen = () => {
    backoffMs = 1000;
  };
  es.onerror = () => {
    es?.close();
    es = null;
    setTimeout(connect, backoffMs);
    backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF);
  };
}

export function initSse() {
  if (!es) connect();
}
