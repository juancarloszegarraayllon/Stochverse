/**
 * Golf block component.
 *
 * Golf is Tier I in DETAILED_EVENT_STATS_SCHEMA.md §1 — a separate
 * endpoint family from team sports. Probe v4 (2026-05-02) confirmed
 * /v1/events/no-duel-data and /v1/events/rounds-results both return
 * data when called with the no_duel_event_id + event_id pair. Probe
 * v2 misclassified golf as Tier F (no FL data) because it used
 * event_id only.
 *
 * Backend wraps both endpoints into /api/event/<t>/golf-detail and
 * does the no_duel_event_id discovery from /v1/events/list. This
 * block renders:
 *   1. Header: tournament round + event metadata from no-duel-data
 *   2. Leaderboard: per-round results table from rounds-results
 *
 * Note: golf response shapes aren't documented in FL OpenAPI (only
 * the params are). The renderer is best-effort. Refine once
 * FL_OBS=1 logs surface real shapes per tournament.
 */

interface FLGolfRanking {
  EVENT_PARTICIPANT_ID?: string;
  EVENT_PARTICIPANT_NAME?: string;
  EVENT_PARTICIPANT_RANKING?: number | string;
  EVENT_PARTICIPANT_COUNTRY?: string;
  EVENT_PARTICIPANT_COUNTRY_ID?: number | string;
  EVENT_PARTICIPANT_STATUS?: string;
  VS?: number | string;
  VE?: number | string;
  // Catch-all
  [key: string]: unknown;
}

interface FLNoDuelData {
  FEATURES?: unknown;
  STAGE?: string;
  EVENT_PARTICIPANT_RANKING?: FLGolfRanking[];
  EVENT_PARTICIPANTS?: Array<{
    PARTICIPANTS?: FLGolfRanking[];
    [key: string]: unknown;
  }>;
  // Catch-all
  [key: string]: unknown;
}

interface FLRoundItem {
  EVENT_PARTICIPANT_NAME?: string;
  EVENT_PARTICIPANT_RANKING?: number | string;
  RESULT?: string | number;
  TO_PAR?: string | number;
  THRU?: string | number;
  [key: string]: unknown;
}

interface FLRoundGroup {
  GOLF_ROUND?: string | number;
  ROUND?: string | number;
  ITEMS?: FLRoundItem[];
  [key: string]: unknown;
}

interface GolfResponse {
  no_duel?: { DATA?: FLNoDuelData } | null;
  rounds?: { DATA?: FLRoundGroup[] | FLRoundItem[] } | null;
  error?: string;
}

function escH(s: string | number | null | undefined): string {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function extractRankings(noDuel: FLNoDuelData | null | undefined): FLGolfRanking[] {
  if (!noDuel) return [];
  // Two observed shapes: top-level EVENT_PARTICIPANT_RANKING list,
  // or nested EVENT_PARTICIPANTS[].PARTICIPANTS[]. Flatten either.
  if (Array.isArray(noDuel.EVENT_PARTICIPANT_RANKING)) {
    return noDuel.EVENT_PARTICIPANT_RANKING;
  }
  const out: FLGolfRanking[] = [];
  if (Array.isArray(noDuel.EVENT_PARTICIPANTS)) {
    for (const p of noDuel.EVENT_PARTICIPANTS) {
      if (Array.isArray(p.PARTICIPANTS)) {
        for (const r of p.PARTICIPANTS) out.push(r);
      }
    }
  }
  return out;
}

function renderHeader(noDuel: FLNoDuelData | null | undefined): string {
  if (!noDuel) return '';
  const stage = noDuel.STAGE || '';
  if (!stage) return '';
  return '<div class="ed-golf-stage">' + escH(String(stage)) + '</div>';
}

function renderLeaderboard(rankings: FLGolfRanking[]): string {
  if (!rankings.length) return '';
  let html = '<div class="ed-golf-section">';
  html += '<div class="ed-golf-head">Leaderboard</div>';
  html += '<div class="ed-golf-table">';
  html += '<div class="ed-golf-row ed-golf-row-head">';
  html += '<span class="ed-golf-rank">#</span>';
  html += '<span class="ed-golf-name">Player</span>';
  html += '<span class="ed-golf-score">To Par</span>';
  html += '<span class="ed-golf-thru">Thru</span>';
  html += '</div>';
  for (const r of rankings) {
    const rank = r.EVENT_PARTICIPANT_RANKING != null ? String(r.EVENT_PARTICIPANT_RANKING) : '';
    const name = r.EVENT_PARTICIPANT_NAME || '';
    const country = r.EVENT_PARTICIPANT_COUNTRY || '';
    const toPar = r.VS != null ? String(r.VS) : '';
    const thru = r.VE != null ? String(r.VE) : '';
    const status = r.EVENT_PARTICIPANT_STATUS || '';
    html += '<div class="ed-golf-row">';
    html += '<span class="ed-golf-rank">' + escH(rank) + '</span>';
    html += '<span class="ed-golf-name">' + escH(String(name));
    if (country) html += ' <span class="ed-golf-country">' + escH(String(country)) + '</span>';
    html += '</span>';
    html += '<span class="ed-golf-score">' + escH(toPar) + '</span>';
    html += '<span class="ed-golf-thru">';
    html += escH(status || thru);
    html += '</span>';
    html += '</div>';
  }
  html += '</div></div>';
  return html;
}

function renderRounds(rounds: GolfResponse['rounds']): string {
  if (!rounds) return '';
  const r = rounds as { DATA?: unknown };
  const arr = Array.isArray(r.DATA) ? r.DATA : [];
  if (!arr.length) return '';
  // Detect grouped (rounds with ITEMS) vs flat list.
  const isGrouped = arr.some((g) => g && typeof g === 'object'
    && Array.isArray((g as FLRoundGroup).ITEMS));
  let html = '<div class="ed-golf-section">';
  html += '<div class="ed-golf-head">Rounds</div>';
  if (isGrouped) {
    for (const group of arr as FLRoundGroup[]) {
      const label = group.GOLF_ROUND != null
        ? `Round ${escH(String(group.GOLF_ROUND))}`
        : (group.ROUND != null ? String(group.ROUND) : 'Round');
      html += '<div class="ed-golf-round-group">';
      html += '<div class="ed-golf-round-head">' + label + '</div>';
      html += '<div class="ed-golf-table">';
      for (const item of (group.ITEMS || [])) {
        html += '<div class="ed-golf-row">';
        html += '<span class="ed-golf-rank">' + escH(String(item.EVENT_PARTICIPANT_RANKING || '')) + '</span>';
        html += '<span class="ed-golf-name">' + escH(String(item.EVENT_PARTICIPANT_NAME || '')) + '</span>';
        html += '<span class="ed-golf-score">' + escH(String(item.RESULT || item.TO_PAR || '')) + '</span>';
        html += '<span class="ed-golf-thru">' + escH(String(item.THRU || '')) + '</span>';
        html += '</div>';
      }
      html += '</div></div>';
    }
  } else {
    html += '<div class="ed-golf-table">';
    for (const item of arr as FLRoundItem[]) {
      html += '<div class="ed-golf-row">';
      html += '<span class="ed-golf-rank">' + escH(String(item.EVENT_PARTICIPANT_RANKING || '')) + '</span>';
      html += '<span class="ed-golf-name">' + escH(String(item.EVENT_PARTICIPANT_NAME || '')) + '</span>';
      html += '<span class="ed-golf-score">' + escH(String(item.RESULT || item.TO_PAR || '')) + '</span>';
      html += '<span class="ed-golf-thru">' + escH(String(item.THRU || '')) + '</span>';
      html += '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

export async function renderGolf(
  mount: HTMLElement,
  ticker: string,
): Promise<void> {
  mount.innerHTML = '<div class="ed-stats-loading">Loading golf data…</div>';
  try {
    const r = await fetch(
      '/api/event/' + encodeURIComponent(ticker) + '/golf-detail',
    );
    const json = (await r.json()) as GolfResponse;
    if (json.error) {
      mount.innerHTML = '<div class="ed-stats-loading">' + escH(json.error) + '</div>';
      return;
    }
    const noDuel = (json.no_duel && (json.no_duel as { DATA?: FLNoDuelData }).DATA) || null;
    const rankings = extractRankings(noDuel);
    let html = '<div class="ed-golf-wrap">';
    html += renderHeader(noDuel);
    html += renderLeaderboard(rankings);
    html += renderRounds(json.rounds);
    html += '</div>';
    if (html === '<div class="ed-golf-wrap"></div>') {
      mount.innerHTML = '<div class="ed-stats-loading">No golf data available.</div>';
      return;
    }
    mount.innerHTML = html;
  } catch (e) {
    mount.innerHTML = '<div class="ed-stats-loading">Failed to load golf data.</div>';
  }
}
