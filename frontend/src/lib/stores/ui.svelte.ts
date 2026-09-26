export type StepId = "book" | "chapters" | "voice" | "sample" | "convert";

class UiStore {
  stepByJob = $state<Record<string, StepId>>({});
  creatingNew = $state(false);

  stepFor(jobId: string): StepId {
    return this.stepByJob[jobId] ?? "book";
  }

  setStep(jobId: string, step: StepId) {
    this.stepByJob = { ...this.stepByJob, [jobId]: step };
  }

  startNewBook() {
    this.creatingNew = true;
  }

  cancelNewBook() {
    this.creatingNew = false;
  }
}

export const uiStore = new UiStore();
