/**
 * Standings block component.
 *
 * Renders the compact `data.standings.overall | home | away | form`
 * shape from /normalized into a per-group league table. Same shape
 * for all four sub-types — only the column header conventions differ
 * by context.
 */
import type { NormalizedEvent } from '../types/normalized';

const STYLE_ID = 'sv-standings-styles';
const STYLES = `
.sv-standings{display:flex;flex-direction:column;gap:14px;padding:8px 4px}
.sv-standings-group-name{font-size:11px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;color:var(--text-dim,#888);padding:0 4px 4px}
.sv-standings-table{width:100%;border-collapse:collapse;font-size:13px}
.sv-standings-table th,.sv-standings-table td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--border,#2a2a2a)}
.sv-standings-table th{font-size:11px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;color:var(--text-dim,#888);border-bottom:1px solid var(--border,#2a2a2a)}
.sv-standings-table td.num,.sv-standings-table th.num{text-align:right;font-variant-numeric:tabular-nums}
.sv-standings-rank{width:32px;color:var(--text-dim,#888);font-variant-numeric:tabular-nums}
.sv-standings-team{font-weight:500}
.sv-standings-q1{box-shadow:inset 3px 0 0 #4a90e2}
.sv-standings-q2{box-shadow:inset 3px 0 0 #2a6fb5}
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

interface StandingsData {
  groups: Array<{
    name: string;
    rows: Array<{
      rank: number;
      name: string;
      team_id: string;
      played: number;
      wins: number;
      goals: string;
      points: number;
      qualification: string | null;
    }>;
  }>;
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
      if (r.qualification === 'q1') tr.classList.add('sv-standings-q1');
      else if (r.qualification === 'q2') tr.classList.add('sv-standings-q2');
      tr.innerHTML =
        '<td class="num sv-standings-rank">' +
        (r.rank ?? '') +
        '</td>' +
        '<td class="sv-standings-team">' +
        escHTML(r.name || '') +
        '</td>' +
        '<td class="num">' +
        (r.played ?? '') +
        '</td>' +
        '<td class="num">' +
        (r.wins ?? '') +
        '</td>' +
        '<td class="num">' +
        escHTML(r.goals || '') +
        '</td>' +
        '<td class="num">' +
        (r.points ?? '') +
        '</td>';
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
  }
  mount.appendChild(wrap);
}

function escHTML(s: string): string {
  return s.replace(/[<>&"']/g, (c) => {
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
