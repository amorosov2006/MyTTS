// In-memory cache of Gemini voice preview object URLs, keyed by voice+style+model, so switching
// tabs or reopening the Voice & style step doesn't re-request audio Gemini already generated.
// Module-level (not a class instance field) so it survives the Voice step component being
// destroyed and recreated when the user navigates away and back.
const cache = new Map<string, string>();

export function previewCacheKey(voice: string, style: string, model: string): string {
  return `${voice}\u0000${style}\u0000${model}`;
}

export function getCachedPreview(key: string): string | undefined {
  return cache.get(key);
}

export function setCachedPreview(key: string, url: string): void {
  cache.set(key, url);
}
