import * as api from "../api";
import type { SystemInfo, WorkerStatus } from "../types";

class SystemStore {
  info = $state<SystemInfo | null>(null);
  worker = $state<WorkerStatus | null>(null);

  async refresh() {
    this.info = await api.getSystem();
    this.worker = this.info.worker;
  }

  applyWorker(w: WorkerStatus) {
    this.worker = w;
  }
}

export const systemStore = new SystemStore();
