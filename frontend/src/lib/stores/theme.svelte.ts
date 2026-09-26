const STORAGE_KEY = "mytts.theme";
type Theme = "light" | "dark";

function systemPrefersDark(): boolean {
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

function loadInitial(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* private mode / blocked storage */
  }
  return systemPrefersDark() ? "dark" : "light";
}

class ThemeStore {
  current = $state<Theme>(loadInitial());

  constructor() {
    $effect.root(() => {
      $effect(() => {
        document.documentElement.setAttribute("data-theme", this.current);
      });
    });
  }

  toggle() {
    this.current = this.current === "dark" ? "light" : "dark";
    try {
      localStorage.setItem(STORAGE_KEY, this.current);
    } catch {
      /* ignore */
    }
  }
}

export const themeStore = new ThemeStore();
