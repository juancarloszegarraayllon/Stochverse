/**
 * Missing Players block component.
 *
 * Renders `data.missing_players` from /normalized — FL's
 * /v1/events/missing-players payload (DATA: [{TEAM, PLAYER_NAME,
 * CHANCE_TO_PLAY, ABSENCE_REASON}]). Going through /normalized (vs
 * the dedicated /api/event/<t>/missing-players endpoint) shares the
 * cross-block 5-min cache; squad availability barely changes once
 * the team sheet is published, so the TTL doesn't shadow updates.
 *
 * Layout matches the legacy inline renderer: per-team section with
 * a header, then a row per missing player showing name + reason +
 * chance-to-play tag (color-coded: red for "out"/"miss", orange
 * for "doubtful", green for "expected"/"likely"). Reuses the inline
 * .ed-stats-title CSS for headers; player rows use inline styles
 * matching the legacy renderer exactly.
 *
 * Home/away team names come from the title (FL ships TEAM=1/2 but
 * not the names themselves at this endpoint). Same parsing the
 * Lineups block uses.
 */
import type { NormalizedEvent } from '../types/normalized';
import { fetchNormalized } from '../api/normalized';

interface MissingPlayer {
  TEAM?: number;
  PLAYER_NAME?: string;
  CHANCE_TO_PLAY?: string;
  ABSENCE_REASON?: string;
}

interface MissingPlayersData {
  DATA?: MissingPlayer[];
  data?: MissingPlayer[];
}

export async function renderMissingPlayers(
  mount: HTMLElement,
  ticker: string,
): Promise<void> {
  mount.innerHTML =
    '<div class="ed-stats-loading">Loading missing players…</div>';
  try {
    const ev: NormalizedEvent = await fetchNormalized(ticker);
    const raw = (ev.data as { missing_players?: unknown }).missing_players;
    let players: MissingPlayer[] = [];
    if (raw && typeof raw === 'object') {
      const r = raw as MissingPlayersData;
      players = r.DATA || r.data || [];
    }
    if (!Array.isArray(players) || players.length === 0) {
      mount.innerHTML =
        '<div class="ed-stats-loading">No missing players reported.</div>';
      return;
    }
    // Parse home/away from title — matches the Lineups block. FL's
    // missing-players endpoint doesn't ship team names, only TEAM=1/2.
    const title = ev.title || '';
    const parts = title.split(/\s+(?:vs\.?|v|at)\s+/i);
    const homeName = parts[0]?.trim() || 'Home';
    const awayName = parts[1]?.trim() || 'Away';

    const homeList = players.filter((p) => p.TEAM === 1);
    const awayList = players.filter((p) => p.TEAM === 2);
    const html = renderTeam(homeName, homeList) + renderTeam(awayName, awayList);
    mount.innerHTML =
      html || '<div class="ed-stats-loading">No missing players reported.</div>';
  } catch {
    mount.innerHTML =
      '<div class="ed-stats-loading">Failed to load missing players.</div>';
  }
}

function renderTeam(name: string, list: MissingPlayer[]): string {
  if (!list.length) return '';
  let h =
    '<div class="ed-stats-title" style="margin-top:14px">' +
    escHTML(name) +
    '</div>';
  for (const p of list) {
    const chance = p.CHANCE_TO_PLAY || '';
    const reason = p.ABSENCE_REASON || '';
    h +=
      '<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);font-size:11px">';
    h +=
      '<div style="flex:1;color:var(--text)">' +
      escHTML(p.PLAYER_NAME || '') +
      '</div>';
    if (reason) {
      h +=
        '<div style="color:var(--text-muted);font-size:10px">' +
        escHTML(reason) +
        '</div>';
    }
    if (chance) {
      h +=
        '<div style="color:' +
        chanceColor(chance) +
        ';font-size:10px;font-weight:700;text-transform:uppercase">' +
        escHTML(chance) +
        '</div>';
    }
    h += '</div>';
  }
  return h;
}

function chanceColor(chance: string): string {
  const c = String(chance || '').toLowerCase();
  if (c.includes('doubt')) return '#ffb74d';
  if (c.includes('out') || c.includes('miss')) return '#f44336';
  if (c.includes('expected') || c.includes('likely')) return '#4caf50';
  return 'var(--text-dim)';
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
