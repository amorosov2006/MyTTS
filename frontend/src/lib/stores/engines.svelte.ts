import * as api from "../api";
import type { EngineInfo, GeminiKeyStatus } from "../types";

class EnginesStore {
  items = $state<EngineInfo[]>([]);
  loaded = $state(false);

  async refresh() {
    this.items = await api.getEngines();
    this.loaded = true;
  }

  get gemini(): EngineInfo | undefined {
    return this.items.find((e) => e.id === "gemini");
  }

  get local(): EngineInfo | undefined {
    return this.items.find((e) => e.id === "local");
  }

  /** Applied after PUT/DELETE /api/keys/gemini so every view (Voice step, Settings modal) stays in sync. */
  setGeminiKeyStatus(status: GeminiKeyStatus) {
    this.items = this.items.map((e) => (e.id === "gemini" ? { ...e, key: status, available: status.configured } : e));
  }
}

export const enginesStore = new EnginesStore();
