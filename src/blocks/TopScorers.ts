/**
 * Top Scorers block component.
 *
 * Renders `data.standings.top_scorers` from /normalized as a flat
 * goals/assists table with a "Show all" expander when has_more is
 * true. Click expands inline by re-fetching with limit=0.
 */
import type { TopScorers } from '../types/normalized';
import { fetchNormalized } from '../api/normalized';

const STYLE_ID = 'sv-top-scorers-styles';
const STYLES = `
.sv-top-scorers{display:flex;flex-direction:column;gap:8px;padding:8px 4px}
.sv-top-scorers-table{width:100%;border-collapse:collapse;font-size:13px}
.sv-top-scorers-table th,.sv-top-scorers-table td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--border,#2a2a2a)}
.sv-top-scorers-table th{font-size:11px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;color:var(--text-dim,#888)}
.sv-top-scorers-table td.num,.sv-top-scorers-table th.num{text-align:right;font-variant-numeric:tabular-nums}
.sv-top-scorers-rank{width:32px;color:var(--text-dim,#888)}
.sv-top-scorers-player{font-weight:500}
.sv-top-scorers-team{color:var(--text-dim,#888);font-size:12px}
.sv-top-scorers-more{margin-top:8px;padding:8px 12px;font-size:12px;color:var(--accent,#3fb950);background:transparent;border:1px solid var(--border,#2a2a2a);border-radius:6px;cursor:pointer;align-self:flex-start}
.sv-top-scorers-more:hover{background:var(--bg-card,#1a1a1a)}
.sv-top-scorers-more:disabled{opacity:.5;cursor:wait}
.sv-top-scorers-empty{color:var(--text-dim,#888);font-size:13px;padding:20px;text-align:center}
`;

function ensureStyles(): void {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = STYLES;
  document.head.appendChild(style);
}

export function renderTopScorers(
  mount: HTMLElement,
  data: TopScorers | null | undefined,
  ticker: string,
): void {
  ensureStyles();
  mount.innerHTML = '';
  if (!data || !Array.isArray(data.rows) || data.rows.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'sv-top-scorers-empty';
    empty.textContent = 'No top scorers available.';
    mount.appendChild(empty);
    return;
  }

  const wrap = document.createElement('div');
  wrap.className = 'sv-top-scorers';

  const table = document.createElement('table');
  table.className = 'sv-top-scorers-table';
  const thead = document.createElement('thead');
  thead.innerHTML =
    '<tr>' +
    '<th class="num">#</th>' +
    '<th>Player</th>' +
    '<th>Team</th>' +
    '<th class="num">G</th>' +
    '<th class="num">A</th>' +
    '</tr>';
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const r of data.rows) {
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td class="num sv-top-scorers-rank">' +
      (r.rank ?? '') +
      '</td>' +
      '<td class="sv-top-scorers-player">' +
      escHTML(r.name || '') +
      '</td>' +
      '<td class="sv-top-scorers-team">' +
      escHTML(r.team || '') +
      '</td>' +
      '<td class="num">' +
      (r.goals ?? '') +
      '</td>' +
      '<td class="num">' +
      (r.assists ?? '') +
      '</td>';
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);

  if (data.has_more) {
    const btn = document.createElement('button');
    btn.className = 'sv-top-scorers-more';
    const remaining = data.total - data.rows.length;
    btn.textContent = `Show all ${data.total} (+${remaining} more)`;
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = 'Loading…';
      try {
        const ev = await fetchNormalized(ticker, { topScorersLimit: 0 });
        renderTopScorers(
          mount,
          (ev.data?.standings as { top_scorers: TopScorers | null })
            ?.top_scorers,
          ticker,
        );
      } catch (ex) {
        btn.disabled = false;
        btn.textContent = `Failed: ${ex instanceof Error ? ex.message : 'retry'}`;
      }
    });
    wrap.appendChild(btn);
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
