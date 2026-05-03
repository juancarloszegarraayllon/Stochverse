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
import { renderSummary } from './blocks/Summary';
import { renderPlayerStats } from './blocks/PlayerStats';
import { renderMissingPlayers } from './blocks/MissingPlayers';
import { renderPredictedLineups } from './blocks/PredictedLineups';
import { renderHighlights } from './blocks/Highlights';
import { renderPointsHistory } from './blocks/PointsHistory';
import { renderDarts } from './blocks/Darts';
import { renderGolf } from './blocks/Golf';

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
      // Render the Match → Summary timeline from /normalized.data.incidents.
      // Backend pre-parses FL summary-incidents into our timeline shape
      // so the block doesn't repeat that work.
      renderSummary?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
      // Render the Match → Player Stats sub-tab from
      // /normalized.data.player_stats. Reads the raw FL payload and
      // groups players by team, with category tabs across the top.
      renderPlayerStats?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
      // Render the Match → Missing sub-tab from
      // /normalized.data.missing_players. Per-team list of unavailable
      // players with chance-to-play color tag.
      renderMissingPlayers?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
      // Render the Match → Predicted Lineups sub-tab from
      // /api/event/<t>/predicted-lineups (dedicated endpoint —
      // predicted_lineups isn't part of /normalized fan-out for
      // cold-start latency reasons). Capability-gated tab.
      renderPredictedLineups?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
      // Render the Match → Highlights sub-tab from
      // /api/event/<t>/highlights. Soccer / Cricket / Aussie
      // Rules / Rugby League per probe v2 inventory.
      // Capability-gated tab.
      renderHighlights?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
      // Render the Match → Points History sub-tab from
      // /api/event/<t>/points-history. Tennis (set/game/point),
      // Basketball (running margin), and a handful of other
      // sports per probe v2. Capability-gated tab.
      renderPointsHistory?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
      // Render the Match → Darts sub-tab from
      // /api/event/<t>/darts-detail. Combined statistics-alt
      // (categorical stats: 180s, checkouts, averages) +
      // throw-by-throw (live progression). Darts-only tab.
      renderDarts?: (
        ticker: string,
        mount: HTMLElement,
      ) => Promise<void>;
      // Render the Match → Golf sub-tab from
      // /api/event/<t>/golf-detail. Tier I sport — uses the
      // no_duel_event_id + event_id pair, separate endpoint
      // family. Combined no-duel-data + rounds-results.
      renderGolf?: (
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
      let ts = (
        ev.data?.standings as { top_scorers?: import('./types/normalized').TopScorers | null }
      )?.top_scorers;
      // Fallback to dedicated /topscorers endpoint when /normalized's
      // probe came back empty (parallel fan-out individual probe
      // failure under FL pressure, OR cached stage_id was for the
      // wrong sub-stage and top_scorers live on a different one).
      if (!ts || !ts.rows || ts.rows.length === 0) {
        try {
          const r = await fetch(
            '/api/event/' + encodeURIComponent(ticker) + '/topscorers',
          );
          if (r.ok) {
            const d = await r.json();
            if (d && !d.error && d.data) {
              // Compact the FL response into the {rows, total, has_more}
              // shape renderTopScorers expects.
              const rawRows: any[] =
                d.data.ROWS ||
                (Array.isArray(d.data.DATA) && d.data.DATA[0]?.ROWS) ||
                [];
              if (rawRows.length > 0) {
                ts = {
                  rows: rawRows.map((r: any) => ({
                    rank: r.TS_RANK,
                    name: r.TS_PLAYER_NAME_PA || r.TS_PLAYER_NAME,
                    team: r.TEAM_NAME,
                    goals: r.TS_PLAYER_GOALS,
                    assists: r.TS_PLAYER_ASISTS,
                  })),
                  total: rawRows.length,
                  has_more: false,
                };
              }
            }
          }
        } catch {
          /* keep the empty-state in renderTopScorers */
        }
      }
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
    let lineups = (ev.data as { lineups?: unknown }).lineups as Parameters<
      typeof renderLineups
    >[1];
    // Fallback to dedicated /stats endpoint when /normalized's
    // lineups probe came back empty (parallel fan-out individual
    // probe failure under FL pressure). /stats parses lineups into
    // the same shape, no adapter needed.
    if (!hasLineupsData(lineups)) {
      try {
        const r = await fetch(
          '/api/event/' + encodeURIComponent(ticker) + '/stats',
        );
        if (r.ok) {
          const d = await r.json();
          if (d && !d.error && hasLineupsData(d.lineups)) {
            lineups = d.lineups as Parameters<typeof renderLineups>[1];
          }
        }
      } catch {
        /* keeps the empty-state in renderLineups */
      }
    }
    renderLineups(mount, lineups, homeName, awayName);
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
    let data = ev.data?.stats as Parameters<typeof renderStats>[1];
    // Fallback to dedicated /stats when /normalized's statistics
    // probe came back empty (parallel fan-out individual probe
    // failure under FL pressure).
    if (!data || !hasStatsData(data)) {
      const fb = await fetchStatsFallback(ticker);
      if (fb) data = fb;
    }
    renderStats(mount, data);
  } catch (ex) {
    const msg = ex instanceof Error ? ex.message : String(ex);
    mount.innerHTML =
      '<div class="ed-stats-loading">Stats failed to load: ' +
      msg.replace(/[<>&]/g, '') +
      '</div>';
  }
}

function hasStatsData(d: unknown): boolean {
  if (!d || typeof d !== 'object') return false;
  const o = d as { stats?: unknown[]; stats_grouped?: unknown[] };
  return (
    (Array.isArray(o.stats) && o.stats.length > 0) ||
    (Array.isArray(o.stats_grouped) && o.stats_grouped.length > 0)
  );
}

function hasLineupsData(d: unknown): boolean {
  if (!d || typeof d !== 'object') return false;
  const o = d as { home?: { players?: unknown[] }; away?: { players?: unknown[] } };
  const homeN = Array.isArray(o.home?.players) ? o.home!.players!.length : 0;
  const awayN = Array.isArray(o.away?.players) ? o.away!.players!.length : 0;
  return homeN > 0 || awayN > 0;
}

async function fetchStatsFallback(
  ticker: string,
): Promise<Parameters<typeof renderStats>[1] | null> {
  try {
    const r = await fetch(
      '/api/event/' + encodeURIComponent(ticker) + '/stats',
    );
    if (!r.ok) return null;
    const d = await r.json();
    if (!d || d.error) return null;
    return d as Parameters<typeof renderStats>[1];
  } catch {
    return null;
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

async function renderSummaryByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  return renderSummary(mount, ticker);
}

async function renderPlayerStatsByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  return renderPlayerStats(mount, ticker);
}

async function renderMissingPlayersByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  return renderMissingPlayers(mount, ticker);
}

async function renderPredictedLineupsByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  return renderPredictedLineups(mount, ticker);
}

async function renderHighlightsByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  return renderHighlights(mount, ticker);
}

async function renderPointsHistoryByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  return renderPointsHistory(mount, ticker);
}

async function renderDartsByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  return renderDarts(mount, ticker);
}

async function renderGolfByTicker(
  ticker: string,
  mount: HTMLElement,
): Promise<void> {
  return renderGolf(mount, ticker);
}

window.StochverseBundle = {
  version: '0.7.18',
  loadedAt: Date.now(),
  renderBracket: renderBracketByTicker,
  renderStandingsType: renderStandingsTypeByTicker,
  renderStats: renderStatsByTicker,
  renderH2H: renderH2HByTicker,
  renderLineups: renderLineupsByTicker,
  renderCommentary: renderCommentaryByTicker,
  stopCommentary: stopCommentaryPoll,
  renderNews: renderNewsByTicker,
  renderSummary: renderSummaryByTicker,
  renderPlayerStats: renderPlayerStatsByTicker,
  renderMissingPlayers: renderMissingPlayersByTicker,
  renderPredictedLineups: renderPredictedLineupsByTicker,
  renderHighlights: renderHighlightsByTicker,
  renderPointsHistory: renderPointsHistoryByTicker,
  renderDarts: renderDartsByTicker,
  renderGolf: renderGolfByTicker,
};

console.log('[stochverse] bundle loaded', window.StochverseBundle);

export {};
