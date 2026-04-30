/**
 * Top Scorers block component.
 *
 * Renders `data.standings.top_scorers` from /normalized as a flat
 * goals/assists table with paginated "Show more" / "Show less"
 * buttons.
 *
 * Pagination: starts at PAGE_SIZE rows, each "Show more" click
 * fetches PAGE_SIZE more from the backend (re-issues /normalized
 * with topScorersLimit=N). "Show less" collapses back to the
 * initial PAGE_SIZE and scrolls the table back into view —
 * matches the H2H "Show less" UX so users have a consistent
 * mental model across blocks.
 */
import type { TopScorers } from '../types/normalized';
import { fetchNormalized } from '../api/normalized';

const PAGE_SIZE = 20;

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
.sv-top-scorers-actions{display:flex;gap:8px;margin-top:8px}
.sv-top-scorers-btn{padding:8px 12px;font-size:12px;color:var(--accent,#3fb950);background:transparent;border:1px solid var(--border,#2a2a2a);border-radius:6px;cursor:pointer}
.sv-top-scorers-btn:hover{background:var(--bg-card,#1a1a1a)}
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

  // Action row holds Show more / Show less side-by-side. Show less
  // only appears when the user has expanded past the initial page.
  const actions = document.createElement('div');
  actions.className = 'sv-top-scorers-actions';
  const shown = data.rows.length;
  const total = data.total;

  if (data.has_more) {
    const remaining = total - shown;
    const nextChunk = Math.min(PAGE_SIZE, remaining);
    const moreBtn = document.createElement('button');
    moreBtn.className = 'sv-top-scorers-btn';
    moreBtn.textContent =
      `Show ${nextChunk} more (${shown} of ${total})`;
    moreBtn.addEventListener('click', () =>
      reload(mount, ticker, shown + PAGE_SIZE, moreBtn),
    );
    actions.appendChild(moreBtn);
  }

  if (shown > PAGE_SIZE) {
    const lessBtn = document.createElement('button');
    lessBtn.className = 'sv-top-scorers-btn';
    lessBtn.textContent = 'Show less';
    lessBtn.addEventListener('click', async () => {
      await reload(mount, ticker, PAGE_SIZE, lessBtn);
      // Scroll the now-shorter table back into view, otherwise the
      // user stays scrolled past where the expanded list used to end.
      // Mirrors the H2H "Show less" UX in static/index.html.
      if (mount.scrollIntoView) {
        mount.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
    actions.appendChild(lessBtn);
  }

  if (actions.children.length > 0) wrap.appendChild(actions);
  mount.appendChild(wrap);
}

async function reload(
  mount: HTMLElement,
  ticker: string,
  limit: number,
  triggerBtn: HTMLButtonElement,
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
