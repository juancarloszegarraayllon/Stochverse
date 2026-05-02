/**
 * Darts block component.
 *
 * Combined darts-specific renderer: shows categorical stats
 * (statistics-alt: 180s, 140+, 100+, checkouts, averages, leg-won)
 * on top, then throw-by-throw live progression below. Single sub-tab
 * "Darts" instead of two — keeps the modal strip clean.
 *
 * Confirmed by probe v4 (2026-05-02) on canonical IDs:
 *   /v1/events/statistics-alt → 5-key shape per stat row
 *     (CATEGORY, ID, VALUE_HOME, VALUE_AWAY)
 *   /v1/events/throw-by-throw → 10 KB { VALUE, BLOCKS } shape
 *
 * Backend wraps both into /api/event/<t>/darts-detail returning
 * { stats, throws }. Capability-gated on
 * /capabilities.darts_stats || /capabilities.throw_by_throw.
 *
 * Note: throw-by-throw response shape isn't documented in FL
 * OpenAPI (only the params are). The renderer is best-effort —
 * BLOCKS is treated as a list of throw groupings, each with an
 * ITEMS array. Refine once FL_OBS=1 logs surface real shapes.
 */

interface FLDartsStat {
  ID?: string | number;
  CATEGORY?: string;
  VALUE_HOME?: string | number;
  VALUE_AWAY?: string | number;
  [key: string]: unknown;
}

interface FLDartsStatStage {
  STAGE_NAME?: string;
  GROUPS?: Array<{
    GROUP_LABEL?: string;
    ITEMS?: FLDartsStat[];
  }>;
  ITEMS?: FLDartsStat[];
  [key: string]: unknown;
}

interface FLThrow {
  TIME?: string | number;
  VALUE?: string | number;
  SCORE?: string | number;
  PLAYER?: string;
  TYPE?: string;
  [key: string]: unknown;
}

interface FLThrowBlock {
  STAGE_NAME?: string;
  GROUP_LABEL?: string;
  NAME?: string;
  ITEMS?: FLThrow[];
  THROWS?: FLThrow[];
  [key: string]: unknown;
}

interface DartsResponse {
  stats?: { DATA?: FLDartsStatStage[] | FLDartsStat[] } | null;
  throws?: { DATA?: FLThrowBlock[] | FLThrow[]; VALUE?: unknown; BLOCKS?: FLThrowBlock[] } | null;
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

function flattenStats(data: unknown): FLDartsStat[] {
  // /statistics-alt may return rows directly under DATA, or grouped
  // under DATA[].GROUPS[].ITEMS like the regular /statistics shape.
  // Flatten both into one list so the table renders uniformly.
  const out: FLDartsStat[] = [];
  if (!Array.isArray(data)) return out;
  for (const row of data) {
    if (!row || typeof row !== 'object') continue;
    const r = row as FLDartsStatStage & FLDartsStat;
    if (Array.isArray(r.GROUPS)) {
      for (const g of r.GROUPS) {
        if (Array.isArray(g.ITEMS)) {
          for (const it of g.ITEMS) out.push(it);
        }
      }
    } else if (Array.isArray(r.ITEMS)) {
      for (const it of r.ITEMS) out.push(it);
    } else if (r.CATEGORY != null
            || r.VALUE_HOME != null || r.VALUE_AWAY != null) {
      out.push(r as FLDartsStat);
    }
  }
  return out;
}

function renderStatsTable(rows: FLDartsStat[]): string {
  if (!rows.length) return '';
  let html = '<div class="ed-darts-stats">';
  html += '<div class="ed-darts-head">Categories</div>';
  html += '<div class="ed-darts-table">';
  for (const row of rows) {
    const cat = row.CATEGORY || '';
    const home = row.VALUE_HOME != null ? String(row.VALUE_HOME) : '';
    const away = row.VALUE_AWAY != null ? String(row.VALUE_AWAY) : '';
    html += '<div class="ed-darts-row">';
    html += '<span class="ed-darts-home">' + escH(home) + '</span>';
    html += '<span class="ed-darts-cat">' + escH(cat) + '</span>';
    html += '<span class="ed-darts-away">' + escH(away) + '</span>';
    html += '</div>';
  }
  html += '</div></div>';
  return html;
}

function renderThrowBlock(block: FLThrowBlock): string {
  const label = block.STAGE_NAME || block.GROUP_LABEL || block.NAME || '';
  const items = (block.ITEMS as FLThrow[] | undefined)
             || (block.THROWS as FLThrow[] | undefined)
             || [];
  let html = '<div class="ed-darts-block">';
  if (label) html += '<div class="ed-darts-block-head">' + escH(label) + '</div>';
  html += '<div class="ed-darts-throws">';
  for (const t of items) {
    const player = t.PLAYER || '';
    const value = t.VALUE != null ? String(t.VALUE) : '';
    const score = t.SCORE != null ? String(t.SCORE) : '';
    const type = t.TYPE || '';
    html += '<div class="ed-darts-throw">';
    if (player) html += '<span class="ed-darts-player">' + escH(player) + '</span>';
    if (value) html += '<span class="ed-darts-value">' + escH(value) + '</span>';
    if (score) html += '<span class="ed-darts-score">' + escH(score) + '</span>';
    if (type) html += '<span class="ed-darts-type">' + escH(type) + '</span>';
    html += '</div>';
  }
  html += '</div></div>';
  return html;
}

function renderThrows(throwsRaw: DartsResponse['throws']): string {
  if (!throwsRaw) return '';
  // Probe v4 reported top-level { VALUE, BLOCKS } for throw-by-throw
  // (not the usual { DATA } wrapper). Handle both shapes defensively.
  let blocks: FLThrowBlock[] = [];
  const r = throwsRaw as { DATA?: unknown; BLOCKS?: unknown };
  if (Array.isArray(r.BLOCKS)) blocks = r.BLOCKS as FLThrowBlock[];
  else if (Array.isArray(r.DATA)) blocks = r.DATA as FLThrowBlock[];
  if (!blocks.length) return '';
  let html = '<div class="ed-darts-progression">';
  html += '<div class="ed-darts-head">Throw-by-throw</div>';
  for (const b of blocks) html += renderThrowBlock(b);
  html += '</div>';
  return html;
}

export async function renderDarts(
  mount: HTMLElement,
  ticker: string,
): Promise<void> {
  mount.innerHTML = '<div class="ed-stats-loading">Loading darts data…</div>';
  try {
    const r = await fetch(
      '/api/event/' + encodeURIComponent(ticker) + '/darts-detail',
    );
    const json = (await r.json()) as DartsResponse;
    if (json.error) {
      mount.innerHTML = '<div class="ed-stats-loading">' + escH(json.error) + '</div>';
      return;
    }
    let html = '<div class="ed-darts-wrap">';
    const statsData = json.stats && (json.stats as { DATA?: unknown }).DATA;
    if (statsData) {
      const flat = flattenStats(statsData);
      html += renderStatsTable(flat);
    }
    html += renderThrows(json.throws);
    html += '</div>';
    if (html === '<div class="ed-darts-wrap"></div>') {
      mount.innerHTML = '<div class="ed-stats-loading">No darts data available.</div>';
      return;
    }
    mount.innerHTML = html;
  } catch (e) {
    mount.innerHTML = '<div class="ed-stats-loading">Failed to load darts data.</div>';
  }
}
