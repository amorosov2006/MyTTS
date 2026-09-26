export interface Toast {
  id: number;
  level: "info" | "warning" | "error" | "success";
  message: string;
}

let nextId = 1;

class ToastStore {
  items = $state<Toast[]>([]);

  push(level: Toast["level"], message: string, ttlMs = 5000) {
    const id = nextId++;
    this.items.push({ id, level, message });
    if (ttlMs > 0) {
      setTimeout(() => this.dismiss(id), ttlMs);
    }
    return id;
  }

  dismiss(id: number) {
    this.items = this.items.filter((t) => t.id !== id);
  }

  info(message: string) {
    return this.push("info", message);
  }
  success(message: string) {
    return this.push("success", message);
  }
  warning(message: string) {
    return this.push("warning", message);
  }
  error(message: string) {
    return this.push("error", message, 8000);
  }
}

export const toastStore = new ToastStore();
