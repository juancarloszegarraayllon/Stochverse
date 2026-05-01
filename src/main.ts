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
import { renderH2H } from './blocks/H2H';
import { renderLineups } from './blocks/Lineups';
import { renderCommentary, stopCommentaryPoll } from './blocks/Commentary';
import { renderNews } from './blocks/News';

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
      // Render the Match → Lineups sub-tab from /normalized.data.lineups.
      // Sport-aware: defaults to soccer-style formation + Starting XI /
      // Substitutes / Coach layout. /normalized's compactor parses
      // FL's lineup response into a clean shape so the block doesn't
      // re-do FL parsing.
      renderLineups?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
      // Commentary is unique among blocks because it auto-polls.
      // The block owns the timer; the inline JS calls stopCommentary
      // on tab switch to release the interval.
      renderCommentary?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
      stopCommentary?: (mount: HTMLElement) => void;
      // Render the News tab from /normalized.data.news. Going through
      // /normalized (vs the dedicated /api/event/<t>/news endpoint)
      // shares the cross-block 5-min cache; news refreshes infrequently
      // so the TTL doesn't shadow updates.
      renderNews?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
      // Render the H2H tab strip + content from /api/event/<t>/h2h.
      // H2H stays on its dedicated endpoint (not /normalized) because
      // its FL response shape and past-event fallback chain are
      // distinct from every other surface — duplicating that logic
      // into /normalized would be redundant.
      renderH2H?: (
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

async function renderLineupsByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  mount.innerHTML =
    '<div class="ed-stats-loading">Loading lineups…</div>';
  try {
    const ev: NormalizedEvent = await fetchNormalized(ticker);
    // Parse home/away from the title — /normalized populates
    // participants[] only for live/loaded matches; for the lineups
    // section we just need labels for the per-side headers, and
    // the title reliably has them in fixture order.
    const title = ev.title || '';
    const parts = title.split(/\s+(?:vs\.?|v|at)\s+/i);
    const homeName = parts[0]?.trim() || 'Home';
    const awayName = parts[1]?.trim() || 'Away';
    renderLineups(
      mount,
      (ev.data as { lineups?: unknown }).lineups as Parameters<typeof renderLineups>[1],
      homeName,
      awayName,
    );
  } catch (ex) {
    const msg = ex instanceof Error ? ex.message : String(ex);
    mount.innerHTML =
      '<div class="ed-stats-loading">Lineups failed to load: ' +
      msg.replace(/[<>&]/g, '') +
      '</div>';
  }
}

async function renderH2HByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  // The block exports renderH2H(mount, ticker) for symmetry with the
  // other internal block APIs; the public hook flips to (ticker,
  // mount) to match StochverseBundle's convention.
  return renderH2H(mount, ticker);
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

async function renderCommentaryByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  // The block exports renderCommentary(mount, ticker) for symmetry
  // with the other internal block APIs (mount-first); the public
  // hook flips to (ticker, mount) to match StochverseBundle's
  // convention.
  return renderCommentary(mount, ticker);
}

async function renderNewsByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  return renderNews(mount, ticker);
}

window.StochverseBundle = {
  version: '0.4.4',
  loadedAt: Date.now(),
  renderBracket: renderBracketByTicker,
  renderStandingsType: renderStandingsTypeByTicker,
  renderStats: renderStatsByTicker,
  renderH2H: renderH2HByTicker,
  renderLineups: renderLineupsByTicker,
  renderCommentary: renderCommentaryByTicker,
  stopCommentary: stopCommentaryPoll,
  renderNews: renderNewsByTicker,
};

console.log('[stochverse] bundle loaded', window.StochverseBundle);

export {};
