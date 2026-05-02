/**
 * Player Stats block component.
 *
 * Renders `data.player_stats` from /normalized — the raw FL
 * /v1/events/player-stats payload (DATA: {PLAYERS, RATINGS, STATS,
 * TEAMS, STATS_TYPE_GROUPS, STATS_TYPES}). Going through /normalized
 * (vs the dedicated /api/event/<t>/player-stats endpoint) shares the
 * cross-block 5-min cache; player-stats updates only on goal/sub
 * incidents, so the TTL doesn't shadow useful changes.
 *
 * Visual matches the legacy inline renderer: category tabs across
 * the top, one panel per category, each panel split by team with a
 * header (crest + name + side) and a stats table per team.
 *
 * Roster sort order (per team):
 *   1. Rated outfielders, descending by rating
 *   2. Unrated outfielders alphabetically
 *   3. Goalkeepers last
 *
 * Reuses .ps-* CSS classes from static/index.html so the visual
 * is identical to the legacy renderer — no separate stylesheet.
 */
import type { NormalizedEvent } from '../types/normalized';
import { fetchNormalized } from '../api/normalized';

interface FLPlayer {
  ID?: string;
  TEAM_ID?: string;
  NAME?: string;
  SHORT_DISPLAY_NAME?: string;
  POSITION_NAME?: string;
  POSITION_IS_GOALKEEPER?: boolean | number;
}

interface FLRating {
  PLAYER_ID?: string;
  VALUE?: string | number;
}

interface FLStat {
  PLAYER_ID?: string;
  TYPE_ID?: string;
  VALUE?: string | number;
}

interface FLTeam {
  ID?: string;
  NAME?: string;
  THREE_CHAR_NAME?: string;
  SIDE?: string;
  IMAGE?: string;
}

interface FLStatType {
  ID?: string;
  LABEL?: string;
}

interface FLStatTypeGroup {
  ID?: string;
  LABEL?: string;
  TYPES?: Array<{ ID?: string }>;
}

interface FLPlayerStatsData {
  PLAYERS?: FLPlayer[];
  RATINGS?: FLRating[];
  STATS?: FLStat[];
  TEAMS?: FLTeam[];
  STATS_TYPE_GROUPS?: FLStatTypeGroup[];
  STATS_TYPES?: FLStatType[];
}

interface PlayerRecord {
  info: FLPlayer;
  rating: number | null;
  stats: Record<string, string | number>;
}

export async function renderPlayerStats(
  mount: HTMLElement,
  ticker: string,
): Promise<void> {
  mount.innerHTML =
    '<div class="ed-stats-loading">Loading player stats…</div>';
  try {
    const ev: NormalizedEvent = await fetchNormalized(ticker);
    let inner = extractInner(
      (ev.data as { player_stats?: unknown }).player_stats,
    );
    // Fallback: when /normalized's player_stats probe came back
    // empty (failed during the parallel fan-out under FL pressure),
    // hit the dedicated endpoint. One probe instead of one of N.
    if (!(inner.PLAYERS || []).length) {
      try {
        const dedicated = await fetch(
          '/api/event/' + encodeURIComponent(ticker) + '/player-stats',
        );
        if (dedicated.ok) {
          const d = await dedicated.json();
          if (d && !d.error) {
            inner = extractInner(d.data);
          }
        }
      } catch {
        /* keep the empty-state below */
      }
    }
    if (!(inner.PLAYERS || []).length) {
      mount.innerHTML =
        '<div class="ed-stats-loading">No player stats available.</div>';
      return;
    }
    renderInto(mount, inner);
  } catch {
    mount.innerHTML =
      '<div class="ed-stats-loading">Failed to load player stats.</div>';
  }
}

function extractInner(raw: unknown): FLPlayerStatsData {
  if (!raw || typeof raw !== 'object') return {};
  const r = raw as Record<string, unknown>;
  return (r.DATA || r.data || r) as FLPlayerStatsData;
}

function renderInto(mount: HTMLElement, inner: FLPlayerStatsData): void {
  const players = inner.PLAYERS || [];
  const ratings = inner.RATINGS || [];
  const stats = inner.STATS || [];
  const teams = inner.TEAMS || [];
  const typeGroups = inner.STATS_TYPE_GROUPS || [];
  const typesCatalog = inner.STATS_TYPES || [];

  // Index stat-type metadata for label/format lookups.
  const typeMeta: Record<string, FLStatType> = {};
  for (const t of typesCatalog) {
    if (t.ID) typeMeta[t.ID] = t;
  }

  // Build per-player record: info + rating + stats keyed by TYPE_ID.
  const byPlayer: Record<string, PlayerRecord> = {};
  for (const p of players) {
    if (p.ID) {
      byPlayer[p.ID] = { info: p, rating: null, stats: {} };
    }
  }
  for (const rt of ratings) {
    if (rt.PLAYER_ID && byPlayer[rt.PLAYER_ID]) {
      const v = parseFloat(String(rt.VALUE ?? ''));
      if (!isNaN(v)) byPlayer[rt.PLAYER_ID].rating = v;
    }
  }
  // FS_RATING also appears in the STATS array — keep both sources in
  // sync so sorting/coloring works whether RATINGS is empty or not.
  for (const s of stats) {
    if (!s.PLAYER_ID) continue;
    const rec = byPlayer[s.PLAYER_ID];
    if (!rec) continue;
    if (s.TYPE_ID) rec.stats[s.TYPE_ID] = s.VALUE ?? '';
    if (s.TYPE_ID === 'FS_RATING' && rec.rating == null) {
      const fv = parseFloat(String(s.VALUE ?? ''));
      if (!isNaN(fv) && fv > 0) rec.rating = fv;
    }
  }

  // Group players by team, preserving HOME → AWAY order.
  const teamsList = teams.slice().sort((a, b) => {
    const rank = (t: FLTeam) =>
      t.SIDE === 'HOME' ? 0 : t.SIDE === 'AWAY' ? 1 : 2;
    return rank(a) - rank(b);
  });
  const byTeam: Record<string, PlayerRecord[]> = {};
  for (const t of teamsList) {
    if (t.ID) byTeam[t.ID] = [];
  }
  for (const p of players) {
    if (!p.ID || !p.TEAM_ID) continue;
    if (byTeam[p.TEAM_ID]) byTeam[p.TEAM_ID].push(byPlayer[p.ID]);
  }
  // Sort each team's players: rated outfielders first (rating desc),
  // then unrated outfielders, GKs last.
  for (const tk of Object.keys(byTeam)) {
    byTeam[tk].sort((a, b) => {
      const aGK = a.info.POSITION_IS_GOALKEEPER ? 1 : 0;
      const bGK = b.info.POSITION_IS_GOALKEEPER ? 1 : 0;
      if (aGK !== bGK) return aGK - bGK;
      const ra = a.rating || 0;
      const rb = b.rating || 0;
      if (ra !== rb) return rb - ra;
      return (a.info.SHORT_DISPLAY_NAME || '').localeCompare(
        b.info.SHORT_DISPLAY_NAME || '',
      );
    });
  }

  // Determine usable category tabs. FlashLive sometimes ships groups
  // like GENERAL that overlap heavily with TOP_STATS — keep them all
  // so users can compare with FlashScore directly.
  const groups: FLStatTypeGroup[] = typeGroups.length
    ? typeGroups
    : [
        {
          ID: 'ALL',
          LABEL: 'Stats',
          TYPES: [
            { ID: 'FS_RATING' },
            { ID: 'SHOTS_TOTAL' },
            { ID: 'PASSES_ACCURACY' },
            { ID: 'TOUCHES_TOTAL' },
          ],
        },
      ];

  // Stable ID for tab buttons so switching doesn't collide across
  // re-renders within the same DOM.
  const uid = 'ps-' + Math.random().toString(36).slice(2, 8);

  let h = '<div class="ps-cat-tabs">';
  for (let gi = 0; gi < groups.length; gi++) {
    const grp = groups[gi];
    h +=
      '<button class="ed-sb-tab' +
      (gi === 0 ? ' active' : '') +
      '" data-ps-grp="' +
      gi +
      '" onclick="window._psSwitchGroup(this)">' +
      escHTML(grp.LABEL || grp.ID || '') +
      '</button>';
  }
  h += '</div>';

  for (let gi = 0; gi < groups.length; gi++) {
    const g = groups[gi];
    const typeIds = (g.TYPES || []).map((t) => t.ID || '');
    h +=
      '<div class="ps-grp-panel" data-ps-grp="' +
      gi +
      '" style="display:' +
      (gi === 0 ? 'block' : 'none') +
      '">';
    for (const team of teamsList) {
      if (!team.ID) continue;
      const roster = byTeam[team.ID] || [];
      if (!roster.length) continue;
      const crestUrl = team.IMAGE
        ? 'https://www.flashscore.com/res/image/data/' + team.IMAGE
        : '';
      h += '<div class="ps-team-header">';
      if (crestUrl) {
        h +=
          '<img class="ps-team-crest" src="' +
          escHTML(crestUrl) +
          '" alt="" loading="lazy">';
      }
      h +=
        '<span class="ps-team-name">' +
        escHTML(team.NAME || team.THREE_CHAR_NAME || '') +
        '</span>';
      h +=
        '<span class="ps-team-side">' + escHTML(team.SIDE || '') + '</span>';
      h += '</div>';
      h += '<div class="ps-table-wrap"><table class="ps-table">';
      h += '<thead><tr><th class="ps-col-name">Player</th>';
      for (const tid of typeIds) {
        const meta = typeMeta[tid] || {};
        let label = meta.LABEL || tid;
        if (tid === 'FS_RATING') label = 'Rtg';
        h +=
          '<th title="' +
          escHTML(meta.LABEL || '') +
          '">' +
          escHTML(label) +
          '</th>';
      }
      h += '</tr></thead><tbody>';
      for (const p of roster) {
        const name = p.info.SHORT_DISPLAY_NAME || p.info.NAME || '';
        const pos = p.info.POSITION_IS_GOALKEEPER
          ? 'GK'
          : p.info.POSITION_NAME
            ? p.info.POSITION_NAME.substring(0, 3).toUpperCase()
            : '';
        h += '<tr>';
        h +=
          '<td class="ps-col-name">' +
          (pos ? '<span class="ps-pos">' + escHTML(pos) + '</span>' : '') +
          '<span class="ps-pname">' +
          escHTML(name) +
          '</span></td>';
        for (const tid of typeIds) {
          const raw = p.stats[tid];
          let cellHTML: string;
          if (tid === 'FS_RATING') {
            const rv = p.rating;
            if (rv != null && rv > 0) {
              const color =
                rv >= 7.5 ? '#4caf50' : rv >= 6.5 ? 'var(--text)' : '#f44336';
              cellHTML =
                '<span style="font-weight:700;color:' +
                color +
                '">' +
                rv.toFixed(1) +
                '</span>';
            } else {
              cellHTML = '<span style="color:var(--text-muted)">-</span>';
            }
          } else if (
            raw == null ||
            raw === '' ||
            raw === '0' ||
            raw === '0%' ||
            raw === '0.00'
          ) {
            cellHTML = '<span style="color:var(--text-muted)">-</span>';
          } else {
            cellHTML = escHTML(String(raw));
          }
          h += '<td>' + cellHTML + '</td>';
        }
        h += '</tr>';
      }
      h += '</tbody></table></div>';
    }
    h += '</div>';
  }
  mount.innerHTML = h;
  mount.dataset.psUid = uid;
}

function escHTML(s: string): string {
  if (s == null) return '';
  return String(s).replace(/[<>&"']/g, (c) => {
    switch (c) {
      case '<':
        return '&lt;';
      case '>':
        return '&gt;';
      case '&':
        return '&amp;';
      case '"':
        return '&quot;';
      case "'":
        return '&#39;';
    }
    return c;
  });
}
