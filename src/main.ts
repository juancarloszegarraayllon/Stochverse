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
import { renderStandings } from './blocks/Standings';
import { renderTopScorers } from './blocks/TopScorers';

declare global {
  interface Window {
    StochverseBundle?: {
      version: string;
      loadedAt: number;
      // Render the bracket into `mount`. Fetches /normalized internally.
      renderBracket?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
      // Render any standings sub-type (overall / home / away / form /
      // top_scores / draw / overall_live) from /normalized. Inline JS
      // dispatches `standingType` from the existing sub-tab strip;
      // this hook owns the rendering for all of them.
      renderStandingsType?: (
        ticker: string,
        mount: HTMLElement,
        standingType: string,
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

async function renderStandingsTypeByTicker(
  ticker: string,
  mount: HTMLElement,
  standingType: string,
): Promise<void> {
  mount.innerHTML = '<div class="ed-stats-loading">Loading…</div>';
  try {
    const ev: NormalizedEvent = await fetchNormalized(ticker);
    if (standingType === 'draw') {
      renderBracket(mount, ev.data?.bracket || null);
      return;
    }
    if (standingType === 'top_scores' || standingType === 'top_scorers') {
      const ts = (
        ev.data?.standings as { top_scorers?: import('./types/normalized').TopScorers | null }
      )?.top_scorers;
      renderTopScorers(mount, ts, ticker);
      return;
    }
    // overall | home | away | form | overall_live — same shape.
    renderStandings(
      mount,
      ev,
      standingType as 'overall' | 'home' | 'away' | 'form' | 'overall_live',
    );
  } catch (ex) {
    const msg = ex instanceof Error ? ex.message : String(ex);
    mount.innerHTML =
      '<div class="ed-stats-loading">Failed to load: ' +
      msg.replace(/[<>&]/g, '') +
      '</div>';
  }
}

window.StochverseBundle = {
  version: '0.3.0',
  loadedAt: Date.now(),
  renderBracket: renderBracketByTicker,
  renderStandingsType: renderStandingsTypeByTicker,
};

console.log('[stochverse] bundle loaded', window.StochverseBundle);

export {};
