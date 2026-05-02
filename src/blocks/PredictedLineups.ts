/**
 * Predicted Lineups block component.
 *
 * Renders the FlashLive /v1/events/predicted-lineups response —
 * pre-match expected XI per team. Universal across team sports
 * (Soccer, Basketball, Hockey, Baseball, AMF, Volleyball, Cricket
 * per probe v2 inventory).
 *
 * Data shape (from probe v4 canonical 27cNiVKa + FL API docs):
 *   DATA: [
 *     {
 *       ID: "<team_id>",
 *       TYPE: "home" | "away",
 *       NAME: "<team name>",
 *       PREDICTED_LINEUP: {
 *         FORMATION: "4-3-3",
 *         GROUPS: [{ GROUP_ID, GROUP_LABEL: "Goalkeepers" | ... }],
 *         PLAYERS: [{
 *           PLAYER_ID, PLAYER_FULL_NAME, SHORT_NAME,
 *           PLAYER_NUMBER, PLAYER_COUNTRY, PLAYER_POSITION_ID,
 *           GROUP_ID
 *         }]
 *       }
 *     }
 *   ]
 *
 * Hits /api/event/<ticker>/predicted-lineups directly (not
 * /normalized) because predicted_lineups is intentionally excluded
 * from the /normalized fan-out for cold-start latency reasons (see
 * main.py:7244-7252). Capability-gated tab so sub-tab only appears
 * when /capabilities.predicted_lineups === true.
 *
 * Reuses .ed-lu-* CSS classes from static/index.html for visual
 * parity with the live Lineups block — no separate stylesheet.
 */

interface FLPlayer {
  PLAYER_ID?: string;
  PLAYER_FULL_NAME?: string;
  SHORT_NAME?: string;
  PLAYER_NUMBER?: number | string;
  PLAYER_COUNTRY?: number | string;
  PLAYER_POSITION_ID?: number | string;
  GROUP_ID?: number | string;
}

interface FLGroup {
  GROUP_ID?: number | string;
  GROUP_LABEL?: string;
}

interface FLPredictedLineup {
  FORMATION?: string;
  GROUPS?: FLGroup[];
  PLAYERS?: FLPlayer[];
}

interface FLPredictedTeam {
  ID?: string;
  TYPE?: string;
  NAME?: string;
  PREDICTED_LINEUP?: FLPredictedLineup;
}

interface PredictedLineupsResponse {
  data?: { DATA?: FLPredictedTeam[] } | FLPredictedTeam[];
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

function renderTeam(team: FLPredictedTeam): string {
  const lineup = team.PREDICTED_LINEUP || {};
  const formation = lineup.FORMATION || '';
  const players = lineup.PLAYERS || [];
  const groups = lineup.GROUPS || [];

  // Index group_id → label so we can section the roster.
  const groupLabel: Record<string, string> = {};
  for (const g of groups) {
    if (g.GROUP_ID != null) groupLabel[String(g.GROUP_ID)] = g.GROUP_LABEL || '';
  }

  // Bucket players by group_id, preserving FL's order within each group.
  const buckets: Record<string, FLPlayer[]> = {};
  const orderedGroupIds: string[] = [];
  for (const p of players) {
    const gid = p.GROUP_ID == null ? '' : String(p.GROUP_ID);
    if (!(gid in buckets)) {
      buckets[gid] = [];
      orderedGroupIds.push(gid);
    }
    buckets[gid].push(p);
  }

  let html = '<div class="ed-lu-side">';
  html += '<div class="ed-lu-side-head">';
  html += '<span class="ed-lu-team">' + escH(team.NAME || '') + '</span>';
  if (formation) {
    html += '<span class="ed-lu-formation">' + escH(formation) + '</span>';
  }
  html += '</div>';

  if (!players.length) {
    html += '<div class="ed-stats-loading">No predicted lineup yet.</div>';
    html += '</div>';
    return html;
  }

  for (const gid of orderedGroupIds) {
    const label = groupLabel[gid] || '';
    if (label) {
      html += '<div class="ed-lu-section">' + escH(label) + '</div>';
    }
    html += '<div class="ed-lu-roster">';
    for (const p of buckets[gid]) {
      const num = p.PLAYER_NUMBER != null && p.PLAYER_NUMBER !== ''
        ? String(p.PLAYER_NUMBER) : '';
      const name = p.SHORT_NAME || p.PLAYER_FULL_NAME || '';
      html += '<div class="ed-lu-player">';
      if (num) html += '<span class="ed-lu-num">' + escH(num) + '</span>';
      html += '<span class="ed-lu-name">' + escH(name) + '</span>';
      html += '</div>';
    }
    html += '</div>';
  }

  html += '</div>';
  return html;
}

export async function renderPredictedLineups(
  mount: HTMLElement,
  ticker: string,
): Promise<void> {
  mount.innerHTML = '<div class="ed-stats-loading">Loading predicted lineups…</div>';
  try {
    const r = await fetch(
      '/api/event/' + encodeURIComponent(ticker) + '/predicted-lineups',
    );
    const json = (await r.json()) as PredictedLineupsResponse;
    if (json.error) {
      mount.innerHTML = '<div class="ed-stats-loading">' + escH(json.error) + '</div>';
      return;
    }
    // FL wraps the team list in {data: {DATA: [...]}} via our
    // /api/.../predicted-lineups handler. Normalize.
    let teams: FLPredictedTeam[] = [];
    const d = json.data;
    if (d && typeof d === 'object' && 'DATA' in d && Array.isArray(d.DATA)) {
      teams = d.DATA as FLPredictedTeam[];
    } else if (Array.isArray(d)) {
      teams = d as FLPredictedTeam[];
    }
    if (!teams.length) {
      mount.innerHTML = '<div class="ed-stats-loading">No predicted lineups available.</div>';
      return;
    }
    let html = '<div class="ed-lu-wrap ed-lu-predicted">';
    for (const team of teams) html += renderTeam(team);
    html += '</div>';
    mount.innerHTML = html;
  } catch (e) {
    mount.innerHTML = '<div class="ed-stats-loading">Failed to load predicted lineups.</div>';
  }
}
