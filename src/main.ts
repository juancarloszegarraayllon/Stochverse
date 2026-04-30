/**
 * Stochverse frontend module entry point.
 *
 * The bundle output (static/dist/main.js) is loaded by index.html
 * alongside the existing inline vanilla-JS code. This module is
 * the migration target — block components and the schema-driven
 * Detailed Event Stats renderer land here over subsequent commits.
 *
 * For now this just sets a window flag so we can verify in the
 * browser console that the bundle deployed and loaded correctly.
 */

declare global {
  interface Window {
    StochverseBundle?: {
      version: string;
      loadedAt: number;
    };
  }
}

window.StochverseBundle = {
  version: '0.1.0',
  loadedAt: Date.now(),
};

console.log('[stochverse] bundle loaded', window.StochverseBundle);

export {};
