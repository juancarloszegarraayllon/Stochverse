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
import { renderStats } from './blocks/Stats';

declare global {
  interface Window {
    StochverseBundle?: {
      version: string;
      loadedAt: number;
      renderBracket?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
      renderStandingsType?: (
        ticker: string,
        mount: HTMLElement,
        standingType: string,
      ) => Promise<void>;
      // Render the Match → Stats sub-tab from /normalized.data.stats.
      // Replaces the legacy inline renderer that read /api/event/<t>/stats
      // directly; the bundle path goes through /normalized so it
      // benefits from the same caching + future-fixture fallbacks.
      renderStats?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
    };
    // Legacy bracket renderer defined inline in static/index.html.
    // We delegate to it for the visual style users prefer, while
    // still feeding it data from the right stage_id via /normalized.
    _renderBracket?: (
      container: HTMLElement,
      data: unknown,
      currentEventId: string,
    ) => void;
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
    // Prefer the legacy inline renderer — users like its visual style
    // (round columns, winner highlighting, status badges, current-match
    // marker). It consumes the raw FL DATA shape, which /normalized
    // now also exposes via data.bracket_raw using the right stage_id
    // (post-league-phase Play Offs for UCL, not the qualifying bracket).
    const raw = (ev.data as Record<string, unknown>)?.bracket_raw;
    if (raw && typeof window._renderBracket === 'function') {
      window._renderBracket(mount, raw, ev.fl_event_id || '');
      return;
    }
    // Fallback to the new TS component if the legacy renderer or the
    // raw payload is missing. Shouldn't fire in practice.
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
      // Delegate to the legacy inline renderer — better visual style.
      const raw = (ev.data as Record<string, unknown>)?.bracket_raw;
      if (raw && typeof window._renderBracket === 'function') {
        window._renderBracket(mount, raw, ev.fl_event_id || '');
      } else {
        renderBracket(mount, ev.data?.bracket || null);
      }
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

async function renderStatsByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  mount.innerHTML = '<div class="ed-stats-loading">Loading stats…</div>';
  try {
    const ev: NormalizedEvent = await fetchNormalized(ticker);
    renderStats(mount, ev.data?.stats as Parameters<typeof renderStats>[1]);
  } catch (ex) {
    const msg = ex instanceof Error ? ex.message : String(ex);
    mount.innerHTML =
      '<div class="ed-stats-loading">Stats failed to load: ' +
      msg.replace(/[<>&]/g, '') +
      '</div>';
  }
}

window.StochverseBundle = {
  version: '0.4.0',
  loadedAt: Date.now(),
  renderBracket: renderBracketByTicker,
  renderStandingsType: renderStandingsTypeByTicker,
  renderStats: renderStatsByTicker,
};

console.log('[stochverse] bundle loaded', window.StochverseBundle);

export {};
