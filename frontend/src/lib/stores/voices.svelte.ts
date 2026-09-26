import * as api from "../api";
import type { Voice } from "../types";

class VoicesStore {
  items = $state<Voice[]>([]);
  loaded = $state(false);

  async refresh() {
    this.items = await api.listVoices();
    this.loaded = true;
  }

  upsert(voice: Voice) {
    const idx = this.items.findIndex((v) => v.id === voice.id);
    if (idx === -1) this.items = [...this.items, voice];
    else this.items = this.items.map((v) => (v.id === voice.id ? voice : v));
  }

  remove(id: string) {
    this.items = this.items.filter((v) => v.id !== id);
  }

  byId(id: string) {
    return this.items.find((v) => v.id === id) ?? null;
  }
}

export const voicesStore = new VoicesStore();
