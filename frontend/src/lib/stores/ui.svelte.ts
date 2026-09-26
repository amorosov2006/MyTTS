export type StepId = "book" | "chapters" | "voice" | "sample" | "convert";

class UiStore {
  stepByJob = $state<Record<string, StepId>>({});
  creatingNew = $state(false);
  /** Library sidebar as a slide-over drawer below the "nav" breakpoint (~960px). */
  sidebarOpen = $state(false);

  stepFor(jobId: string): StepId {
    return this.stepByJob[jobId] ?? "book";
  }

  setStep(jobId: string, step: StepId) {
    this.stepByJob = { ...this.stepByJob, [jobId]: step };
  }

  startNewBook() {
    this.creatingNew = true;
    this.sidebarOpen = false;
  }

  cancelNewBook() {
    this.creatingNew = false;
  }

  openSidebar() {
    this.sidebarOpen = true;
  }

  closeSidebar() {
    this.sidebarOpen = false;
  }

  toggleSidebar() {
    this.sidebarOpen = !this.sidebarOpen;
  }
}

export const uiStore = new UiStore();
