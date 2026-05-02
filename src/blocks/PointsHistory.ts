/**
 * Points History block component.
 *
 * Renders /v1/events/points-history. Per probe v2 inventory, the
 * endpoint populates for: Tennis, Basketball, Handball, Volleyball,
 * Snooker, Beach Volleyball, Aussie Rules. Response shape varies by
 * sport (per DETAILED_EVENT_STATS_SCHEMA.md §5):
 *
 *   Tennis:
 *     CURRENT_GAME, FIFTEENS_CONTENT (set/game/point breakdown),
 *     SERVING, LOST_SERVE, LAST_SCORED
 *   Basketball:
 *     HOME_AHEAD running-margin field
 *
 * We don't have machine-readable response schemas yet (FL OpenAPI
 * defines parameters but not response bodies — see
 * DETAILED_EVENT_STATS_SCHEMA.md mental-model fix), so this block
 * does best-effort generic rendering: list each entry FL returns
 * with its primary fields, sport-aware where we can detect the
 * shape, generic where we can't. FL_OBS=1 logs from the backend
 * /api/event/<t>/points-history call accumulate the per-sport
 * shapes we'll see in production — refine the renderer once we
 * have real data.
 *
 * Hits /api/event/<t>/points-history directly. Capability-gated.
 */

interface FLPointEntry {
  // Common across sports — best guesses from schema doc §5.
  TIME?: string | number;
  SCORE_HOME?: string | number;
  SCORE_AWAY?: string | number;
  TYPE?: string;
  // Tennis-specific
  CURRENT_GAME?: string;
  FIFTEENS_CONTENT?: string;
  SERVING?: string | number;
  LOST_SERVE?: boolean | number;
  LAST_SCORED?: string | number;
  // Basketball-specific
  HOME_AHEAD?: string | number;
  // Catch-all for unknown fields the renderer doesn't know about.
  [key: string]: unknown;
}

interface FLPointsGroup {
  // FL often groups points into stages/sets (e.g. "1st Set", "2nd
  // Set"). Generic GROUP/STAGE fields plus an items array.
  STAGE_NAME?: string;
  GROUP_LABEL?: string;
  NAME?: string;
  ITEMS?: FLPointEntry[];
  POINTS?: FLPointEntry[];
  [key: string]: unknown;
}

type FLPointsData = FLPointsGroup[] | FLPointEntry[];

interface PointsHistoryResponse {
  data?: { DATA?: FLPointsData } | FLPointsData;
  sport?: string;
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

function isGroupShape(arr: unknown[]): arr is FLPointsGroup[] {
  // Heuristic: if any element has a sub-items array, treat as
  // groups. Otherwise treat as flat entries.
  for (const el of arr) {
    if (el && typeof el === 'object') {
      const e = el as FLPointsGroup;
      if (Array.isArray(e.ITEMS) || Array.isArray(e.POINTS)) return true;
    }
  }
  return false;
}

function renderEntryTennis(entry: FLPointEntry): string {
  const time = entry.TIME != null ? String(entry.TIME) : '';
  const sh = entry.SCORE_HOME != null ? String(entry.SCORE_HOME) : '';
  const sa = entry.SCORE_AWAY != null ? String(entry.SCORE_AWAY) : '';
  const game = entry.CURRENT_GAME || entry.FIFTEENS_CONTENT || '';
  const lostServe = entry.LOST_SERVE === true || entry.LOST_SERVE === 1;
  const score = sh || sa ? `${escH(sh)}–${escH(sa)}` : '';
  let row = '<div class="ed-ph-row' + (lostServe ? ' ed-ph-break' : '') + '">';
  if (time) row += '<span class="ed-ph-time">' + escH(time) + '</span>';
  if (score) row += '<span class="ed-ph-score">' + score + '</span>';
  if (game) row += '<span class="ed-ph-detail">' + escH(String(game)) + '</span>';
  if (lostServe) row += '<span class="ed-ph-tag">Break</span>';
  row += '</div>';
  return row;
}

function renderEntryGeneric(entry: FLPointEntry): string {
  const time = entry.TIME != null ? String(entry.TIME) : '';
  const sh = entry.SCORE_HOME != null ? String(entry.SCORE_HOME) : '';
  const sa = entry.SCORE_AWAY != null ? String(entry.SCORE_AWAY) : '';
  const ahead = entry.HOME_AHEAD != null ? String(entry.HOME_AHEAD) : '';
  const type = entry.TYPE || '';
  const score = sh || sa ? `${escH(sh)}–${escH(sa)}` : '';
  let row = '<div class="ed-ph-row">';
  if (time) row += '<span class="ed-ph-time">' + escH(time) + '</span>';
  if (score) row += '<span class="ed-ph-score">' + score + '</span>';
  if (ahead) row += '<span class="ed-ph-detail">+' + escH(ahead) + '</span>';
  if (type) row += '<span class="ed-ph-detail">' + escH(type) + '</span>';
  row += '</div>';
  return row;
}

function renderEntries(entries: FLPointEntry[], isTennis: boolean): string {
  if (!entries.length) return '';
  let html = '<div class="ed-ph-list">';
  const fn = isTennis ? renderEntryTennis : renderEntryGeneric;
  for (const e of entries) html += fn(e);
  html += '</div>';
  return html;
}

function renderGroup(group: FLPointsGroup, isTennis: boolean): string {
  const label = group.STAGE_NAME || group.GROUP_LABEL || group.NAME || '';
  const entries = (group.ITEMS as FLPointEntry[] | undefined)
                || (group.POINTS as FLPointEntry[] | undefined)
                || [];
  let html = '<div class="ed-ph-group">';
  if (label) html += '<div class="ed-ph-group-head">' + escH(label) + '</div>';
  html += renderEntries(entries, isTennis);
  html += '</div>';
  return html;
}

export async function renderPointsHistory(
  mount: HTMLElement,
  ticker: string,
): Promise<void> {
  mount.innerHTML = '<div class="ed-stats-loading">Loading points history…</div>';
  try {
    const r = await fetch(
      '/api/event/' + encodeURIComponent(ticker) + '/points-history',
    );
    const json = (await r.json()) as PointsHistoryResponse;
    if (json.error) {
      mount.innerHTML = '<div class="ed-stats-loading">' + escH(json.error) + '</div>';
      return;
    }
    let arr: unknown[] = [];
    const d = json.data;
    if (d && typeof d === 'object' && !Array.isArray(d) && Array.isArray(d.DATA)) {
      arr = d.DATA;
    } else if (Array.isArray(d)) {
      arr = d;
    }
    if (!arr.length) {
      mount.innerHTML = '<div class="ed-stats-loading">No points history available.</div>';
      return;
    }
    const sport = (json.sport || '').toLowerCase();
    const isTennis = sport === 'tennis';
    let html = '<div class="ed-ph-wrap">';
    if (isGroupShape(arr)) {
      for (const g of arr as FLPointsGroup[]) html += renderGroup(g, isTennis);
    } else {
      html += renderEntries(arr as FLPointEntry[], isTennis);
    }
    html += '</div>';
    mount.innerHTML = html;
  } catch (e) {
    mount.innerHTML = '<div class="ed-stats-loading">Failed to load points history.</div>';
  }
}
