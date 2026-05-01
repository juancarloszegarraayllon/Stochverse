/**
 * Standings block component.
 *
 * Renders `data.standings.overall | home | away | form` from
 * /normalized as a per-group league table. Includes:
 *   - team logo column (img with graceful fallback when missing)
 *   - row highlighting for the current event's teams (parsed from
 *     ev.title; works even for future fixtures FL hasn't loaded)
 *   - qualification legend (FL META → q1/q2 color + label)
 *   - tie-breaker note (FL META.DECISIONS)
 */
import type { NormalizedEvent } from '../types/normalized';

const STYLE_ID = 'sv-standings-styles';
const STYLES = `
.sv-standings{display:flex;flex-direction:column;gap:14px;padding:8px 4px}
.sv-standings-group-name{font-size:11px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;color:var(--text-dim,#888);padding:0 4px 4px}
.sv-standings-table{width:100%;border-collapse:collapse;font-size:13px}
.sv-standings-table th,.sv-standings-table td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--border,#1a1a1a)}
.sv-standings-table th{font-size:11px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;color:var(--text-dim,#888)}
.sv-standings-table td.num,.sv-standings-table th.num{text-align:right;font-variant-numeric:tabular-nums}
.sv-standings-rank{width:32px;color:var(--text-dim,#888);font-variant-numeric:tabular-nums}
.sv-standings-q-cell{width:6px;padding:0 !important;border-right:none}
.sv-standings-q-bar{display:block;width:3px;height:20px;border-radius:1px}
.sv-standings-team-cell{display:flex;align-items:center;gap:8px;min-width:0}
.sv-standings-logo{flex:0 0 18px;width:18px;height:18px;object-fit:contain;border-radius:2px;background:transparent}
.sv-standings-logo-fallback{flex:0 0 18px;width:18px;height:18px;border-radius:2px;background:var(--bg-card,#1a1a1a)}
.sv-standings-team-name{font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sv-standings-table tr.sv-standings-current{background:rgba(0,255,0,.06)}
.sv-standings-table tr.sv-standings-current td{border-color:rgba(0,255,0,.18)}
.sv-standings-table tr.sv-standings-current .sv-standings-team-name{color:var(--green,#00ff00);font-weight:600}
.sv-standings-meta{display:flex;flex-direction:column;gap:6px;padding:8px 4px 4px;font-size:11px;color:var(--text-dim,#888)}
.sv-standings-legend{display:flex;flex-wrap:wrap;gap:14px}
.sv-standings-legend-item{display:flex;align-items:center;gap:6px}
.sv-standings-legend-swatch{display:inline-block;width:12px;height:12px;border-radius:2px;flex:0 0 12px}
.sv-standings-decision{font-style:italic;line-height:1.4}
.sv-standings-empty{color:var(--text-dim,#888);font-size:13px;padding:20px;text-align:center}
`;

function ensureStyles(): void {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = STYLES;
  document.head.appendChild(style);
}

type StandingsKey = 'overall' | 'home' | 'away' | 'form' | 'overall_live';

interface StandingsRow {
  rank: number;
  name: string;
  team_id: string;
  image_url: string;
  played: number;
  wins: number;
  goals: string;
  points: number;
  qualification: string | null;
  tuc: string;
}

interface StandingsGroup {
  name: string;
  rows: StandingsRow[];
}

interface StandingsMeta {
  qualification_legend?: Array<{
    color: string;
    qualification: string;
    label: string;
  }>;
  decisions?: string[];
}

interface StandingsData {
  groups: StandingsGroup[];
  meta?: StandingsMeta;
}

export function renderStandings(
  mount: HTMLElement,
  ev: NormalizedEvent,
  type: StandingsKey,
): void {
  ensureStyles();
  mount.innerHTML = '';
  const data = (ev.data?.standings as Record<string, StandingsData | null>)?.[
    type
  ];
  if (!data || !Array.isArray(data.groups) || data.groups.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'sv-standings-empty';
    empty.textContent = 'No standings available.';
    mount.appendChild(empty);
    return;
  }

  // Derive which two teams to highlight from the event title. For
  // future fixtures FL hasn't loaded, ev.participants is empty so
  // we can't read team IDs from there — title parsing is the
  // reliable signal both for current and future events.
  const eventTeams = parseTeamsFromTitle(ev.title);

  const wrap = document.createElement('div');
  wrap.className = 'sv-standings';

  for (const grp of data.groups) {
    if (data.groups.length > 1 || (grp.name && grp.name !== 'Main')) {
      const head = document.createElement('div');
      head.className = 'sv-standings-group-name';
      head.textContent = grp.name;
      wrap.appendChild(head);
    }

    const table = document.createElement('table');
    table.className = 'sv-standings-table';

    const thead = document.createElement('thead');
    thead.innerHTML =
      '<tr>' +
      '<th class="num">#</th>' +
      '<th class="sv-standings-q-cell"></th>' +
      '<th>Team</th>' +
      '<th class="num">MP</th>' +
      '<th class="num">W</th>' +
      '<th class="num">G</th>' +
      '<th class="num">Pts</th>' +
      '</tr>';
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    for (const r of grp.rows) {
      const tr = document.createElement('tr');
      if (matchesEventTeam(r.name, eventTeams)) {
        tr.classList.add('sv-standings-current');
      }
      tr.appendChild(td('num sv-standings-rank', String(r.rank ?? '')));

      // Qualification color stripe (replaces the old left-edge tint
      // with a thin, FL-color-accurate bar so q1 / q2 look like
      // FlashScore renders them).
      const qCell = document.createElement('td');
      qCell.className = 'sv-standings-q-cell';
      if (r.tuc) {
        const bar = document.createElement('span');
        bar.className = 'sv-standings-q-bar';
        bar.style.background = '#' + r.tuc;
        qCell.appendChild(bar);
      }
      tr.appendChild(qCell);

      const teamCell = document.createElement('td');
      const teamWrap = document.createElement('div');
      teamWrap.className = 'sv-standings-team-cell';
      if (r.image_url) {
        const img = document.createElement('img');
        img.className = 'sv-standings-logo';
        img.src = r.image_url;
        img.alt = '';
        img.loading = 'lazy';
        img.addEventListener(
          'error',
          () => {
            img.replaceWith(makeFallbackLogo());
          },
          { once: true },
        );
        teamWrap.appendChild(img);
      } else {
        teamWrap.appendChild(makeFallbackLogo());
      }
      const name = document.createElement('span');
      name.className = 'sv-standings-team-name';
      name.textContent = r.name || '';
      teamWrap.appendChild(name);
      teamCell.appendChild(teamWrap);
      tr.appendChild(teamCell);

      tr.appendChild(td('num', String(r.played ?? '')));
      tr.appendChild(td('num', String(r.wins ?? '')));
      tr.appendChild(td('num', r.goals || ''));
      tr.appendChild(td('num', String(r.points ?? '')));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
  }

  // Footer: qualification legend + tie-breaker note (FL META).
  const meta = data.meta;
  if (meta && (meta.qualification_legend?.length || meta.decisions?.length)) {
    const footer = document.createElement('div');
    footer.className = 'sv-standings-meta';
    if (meta.qualification_legend?.length) {
      const legend = document.createElement('div');
      legend.className = 'sv-standings-legend';
      for (const item of meta.qualification_legend) {
        const row = document.createElement('div');
        row.className = 'sv-standings-legend-item';
        const swatch = document.createElement('span');
        swatch.className = 'sv-standings-legend-swatch';
        swatch.style.background = '#' + item.color;
        const label = document.createElement('span');
        label.textContent = item.label;
        row.appendChild(swatch);
        row.appendChild(label);
        legend.appendChild(row);
      }
      footer.appendChild(legend);
    }
    if (meta.decisions?.length) {
      for (const d of meta.decisions) {
        const note = document.createElement('div');
        note.className = 'sv-standings-decision';
        note.textContent = d;
        footer.appendChild(note);
      }
    }
    wrap.appendChild(footer);
  }

  mount.appendChild(wrap);
}

function td(className: string, text: string): HTMLTableCellElement {
  const el = document.createElement('td');
  el.className = className;
  el.textContent = text;
  return el;
}

function makeFallbackLogo(): HTMLElement {
  const el = document.createElement('span');
  el.className = 'sv-standings-logo-fallback';
  return el;
}

function parseTeamsFromTitle(title: string): string[] {
  if (!title) return [];
  const parts = title.split(/\s+(?:vs\.?|v|at)\s+/i);
  if (parts.length < 2) return [];
  return parts.map((p) => p.trim().toLowerCase()).filter(Boolean);
}

function matchesEventTeam(rowName: string, eventTeams: string[]): boolean {
  if (!eventTeams.length || !rowName) return false;
  const n = rowName.toLowerCase();
  for (const t of eventTeams) {
    if (!t) continue;
    if (n === t || n.includes(t) || t.includes(n)) return true;
    // Leading-word match — "Bayern" → "Bayern Munich", "Atl. Madrid"
    // → "Atletico Madrid" via first word "atl".
    const firstRow = n.split(/\s+/)[0];
    const firstEv = t.split(/\s+/)[0];
    if (firstRow && firstEv && firstRow === firstEv) return true;
  }
  return false;
}
