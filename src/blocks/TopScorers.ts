/**
 * Top Scorers block component.
 *
 * Single-button pagination, mirroring the H2H "Show more / Show less"
 * UX in static/index.html:
 *
 *   1. Initial state: 20 rows, button = "Show 20 more (20 of 200)".
 *   2. Each click loads PAGE_SIZE more rows and updates the button
 *      label until the full list is on screen.
 *   3. When all rows are visible, the same button morphs to
 *      "Show less"; clicking collapses to PAGE_SIZE and scrolls the
 *      table back into view.
 *
 * No second button — there's only ever one action available, the
 * label tells you what it does.
 */
import type { TopScorers } from '../types/normalized';
import { fetchNormalized } from '../api/normalized';

const PAGE_SIZE = 20;

const STYLE_ID = 'sv-top-scorers-styles';
const STYLES = `
.sv-top-scorers{display:flex;flex-direction:column;gap:8px;padding:8px 4px}
.sv-top-scorers-table{width:100%;border-collapse:collapse;font-size:13px}
.sv-top-scorers-table th,.sv-top-scorers-table td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--border,#1a1a1a)}
.sv-top-scorers-table th{font-size:11px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;color:var(--text-dim,#888)}
.sv-top-scorers-table td.num,.sv-top-scorers-table th.num{text-align:right;font-variant-numeric:tabular-nums}
.sv-top-scorers-rank{width:32px;color:var(--text-dim,#888)}
.sv-top-scorers-player{font-weight:500}
.sv-top-scorers-team{color:var(--text-dim,#888);font-size:12px}
.sv-top-scorers-btn{margin-top:8px;padding:8px 14px;font-size:12px;font-weight:600;color:var(--green,#00ff00);background:transparent;border:1px solid var(--green,#00ff00);border-radius:6px;cursor:pointer;align-self:flex-start;font-family:inherit;letter-spacing:.3px}
.sv-top-scorers-btn:hover{background:rgba(0,255,0,.08)}
.sv-top-scorers-btn:disabled{opacity:.5;cursor:wait}
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

  // Single-button pagination. has_more=true means more rows exist
  // server-side; otherwise (fully expanded), button morphs to
  // "Show less" if we've grown past the initial PAGE_SIZE.
  const shown = data.rows.length;
  const total = data.total;
  if (data.has_more) {
    const remaining = total - shown;
    const nextChunk = Math.min(PAGE_SIZE, remaining);
    const btn = document.createElement('button');
    btn.className = 'sv-top-scorers-btn';
    btn.textContent =
      `Show ${nextChunk} more (${shown} of ${total})`;
    btn.addEventListener('click', () =>
      reload(mount, ticker, shown + PAGE_SIZE, btn, false),
    );
    wrap.appendChild(btn);
  } else if (shown > PAGE_SIZE) {
    const btn = document.createElement('button');
    btn.className = 'sv-top-scorers-btn';
    btn.textContent = 'Show less';
    btn.addEventListener('click', () =>
      reload(mount, ticker, PAGE_SIZE, btn, true),
    );
    wrap.appendChild(btn);
  }

  mount.appendChild(wrap);
}

async function reload(
  mount: HTMLElement,
  ticker: string,
  limit: number,
  triggerBtn: HTMLButtonElement,
  scrollBackOnDone: boolean,
): Promise<void> {
  const original = triggerBtn.textContent;
  triggerBtn.disabled = true;
  triggerBtn.textContent = 'Loading…';
  try {
    const ev = await fetchNormalized(ticker, { topScorersLimit: limit });
    const ts = (
      ev.data?.standings as { top_scorers?: TopScorers | null }
    )?.top_scorers;
    renderTopScorers(mount, ts, ticker);
    if (scrollBackOnDone && mount.scrollIntoView) {
      // Match H2H "Show less" behavior: scroll the table back into
      // view so the user isn't left scrolled past where the expanded
      // list used to end.
      mount.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  } catch (ex) {
    triggerBtn.disabled = false;
    triggerBtn.textContent = original || 'Retry';
    const msg = ex instanceof Error ? ex.message : 'failed';
    console.warn('[stochverse] top scorers reload failed', msg);
  }
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
