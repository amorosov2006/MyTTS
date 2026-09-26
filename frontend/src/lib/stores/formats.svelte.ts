import * as api from "../api";

class FormatsStore {
  extensions = $state<string[]>([]);
  loaded = $state(false);

  async refresh() {
    if (this.loaded) return;
    const res = await api.getFormats();
    this.extensions = res.extensions;
    this.loaded = true;
  }
}

export const formatsStore = new FormatsStore();
