/**
 * Stochverse frontend bundle entry point.
 *
 * Block components are loaded via window.StochverseBundle.* hooks
 * that the inline JS in static/index.html calls when the relevant
 * tab/section is shown. If the bundle fails to load (network, parse
 * error, etc.), the inline JS falls back to its legacy renderer —
 * so the migration is incremental and revertible per-block.
 */
import type { NormalizedEvent } from './types/normalized';
import { fetchNormalized } from './api/normalized';
import { renderBracket } from './blocks/Bracket';

declare global {
  interface Window {
    StochverseBundle?: {
      version: string;
      loadedAt: number;
      // Render the current event's bracket into `mount`. Fetches
      // /normalized internally; caller doesn't need to manage data.
      renderBracket?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
    };
  }
}

async function renderBracketByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  mount.innerHTML =
    '<div class="ed-stats-loading">Loading bracket…</div>';
  try {
    const ev: NormalizedEvent = await fetchNormalized(ticker);
    renderBracket(mount, ev.data?.bracket || null);
  } catch (ex) {
    const msg = ex instanceof Error ? ex.message : String(ex);
    mount.innerHTML =
      '<div class="ed-stats-loading">Bracket failed to load: ' +
      msg.replace(/[<>&]/g, '') +
      '</div>';
  }
}

window.StochverseBundle = {
  version: '0.2.0',
  loadedAt: Date.now(),
  renderBracket: renderBracketByTicker,
};

console.log('[stochverse] bundle loaded', window.StochverseBundle);

export {};
