/**
 * Bracket block component.
 *
 * Renders the compact `data.bracket` shape from /normalized into a
 * horizontal column-per-round layout. First TS block component in
 * the Phase 3 migration; serves as the template for subsequent
 * blocks (Standings, TopScorers, etc.).
 *
 * Reuses CSS variables from the host page (--text-dim, --bg-card,
 * etc.) but namespaces all selectors under .sv-bracket so it
 * doesn't collide with the legacy .ed-bracket renderer that lives
 * in static/index.html.
 */
import type { BracketData, BracketPair } from '../types/normalized';

const STYLE_ID = 'sv-bracket-styles';
const STYLES = `
.sv-bracket{display:flex;gap:24px;overflow-x:auto;padding:8px 4px 16px}
.sv-bracket-round{display:flex;flex-direction:column;gap:12px;min-width:220px;flex:0 0 auto}
.sv-bracket-round-label{font-size:11px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;color:var(--text-dim,#888);padding:0 4px 4px}
.sv-bracket-pair{background:var(--bg-card,#1a1a1a);border:1px solid var(--border,#2a2a2a);border-radius:8px;padding:8px 10px;display:flex;flex-direction:column;gap:4px}
.sv-bracket-pair.sv-bracket-pending{opacity:.85}
.sv-bracket-team{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:13px}
.sv-bracket-team-name{flex:1 1 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sv-bracket-team-score{font-variant-numeric:tabular-nums;color:var(--text-dim,#888);font-size:12px}
.sv-bracket-winner .sv-bracket-team-name{font-weight:600;color:var(--text,#eee)}
.sv-bracket-winner .sv-bracket-team-score{color:var(--text,#eee)}
.sv-bracket-tbd .sv-bracket-team-name{color:var(--text-dim,#888);font-style:italic}
.sv-bracket-empty{color:var(--text-dim,#888);font-size:13px;padding:20px;text-align:center}
.sv-bracket-meta{font-size:11px;color:var(--text-dim,#888);padding-top:4px;border-top:1px solid var(--border,#2a2a2a);margin-top:2px}
`;

function ensureStyles(): void {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = STYLES;
  document.head.appendChild(style);
}

export function renderBracket(
  mount: HTMLElement,
  data: BracketData | null | undefined,
): void {
  ensureStyles();
  mount.innerHTML = '';
  if (!data || !Array.isArray(data.rounds) || data.rounds.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'sv-bracket-empty';
    empty.textContent = 'No bracket data.';
    mount.appendChild(empty);
    return;
  }

  // Highest round_num is earliest round (e.g. 7 = 1/16-finals, 1 = Final).
  const rounds = [...data.rounds].sort((a, b) => b.round_num - a.round_num);

  const wrap = document.createElement('div');
  wrap.className = 'sv-bracket';
  for (const r of rounds) {
    const col = document.createElement('div');
    col.className = 'sv-bracket-round';

    const head = document.createElement('div');
    head.className = 'sv-bracket-round-label';
    head.textContent = r.label || `Round ${r.round_num}`;
    col.appendChild(head);

    if (!r.pairs || r.pairs.length === 0) {
      const tbd = document.createElement('div');
      tbd.className = 'sv-bracket-pair sv-bracket-pending';
      const inner = document.createElement('div');
      inner.className = 'sv-bracket-team sv-bracket-tbd';
      const n = document.createElement('span');
      n.className = 'sv-bracket-team-name';
      n.textContent = 'Awaiting earlier rounds';
      inner.appendChild(n);
      tbd.appendChild(inner);
      col.appendChild(tbd);
    } else {
      for (const p of r.pairs) col.appendChild(renderPair(p));
    }
    wrap.appendChild(col);
  }
  mount.appendChild(wrap);
}

function renderPair(p: BracketPair): HTMLElement {
  const card = document.createElement('div');
  card.className = 'sv-bracket-pair';
  if (!p.winner) card.classList.add('sv-bracket-pending');

  card.appendChild(
    teamRow(
      displayName(p.home_name, p.home),
      p.agg_home,
      p.legs.map((l) => l.home),
      p.winner === 'home',
    ),
  );
  card.appendChild(
    teamRow(
      displayName(p.away_name, p.away),
      p.agg_away,
      p.legs.map((l) => l.away),
      p.winner === 'away',
    ),
  );

  // Per-pair meta: "Apr 30, 21:00" if scheduled and not yet decided.
  if (p.starts_at && !p.winner) {
    const meta = document.createElement('div');
    meta.className = 'sv-bracket-meta';
    meta.textContent = formatStartsAt(p.starts_at);
    card.appendChild(meta);
  }
  return card;
}

function teamRow(
  name: string,
  agg: number | null,
  legs: number[],
  isWinner: boolean,
): HTMLElement {
  const row = document.createElement('div');
  row.className = 'sv-bracket-team';
  if (isWinner) row.classList.add('sv-bracket-winner');
  if (name === 'TBD') row.classList.add('sv-bracket-tbd');

  const nameEl = document.createElement('span');
  nameEl.className = 'sv-bracket-team-name';
  nameEl.textContent = name;
  row.appendChild(nameEl);

  const scoreEl = document.createElement('span');
  scoreEl.className = 'sv-bracket-team-score';
  if (agg !== null && agg !== undefined) {
    const legPart = legs.length > 1 ? ` (${legs.join('+')})` : '';
    scoreEl.textContent = `${agg}${legPart}`;
  }
  row.appendChild(scoreEl);
  return row;
}

function displayName(
  name: string | null | undefined,
  slug: string | null | undefined,
): string {
  if (name) return name;
  if (!slug) return 'TBD';
  // Fallback humanizer for slugs the FL TABS map didn't cover.
  // "bayern-munich" → "Bayern Munich"; preserve common abbreviations.
  const ABBREV: Record<string, string> = {
    fc: 'FC',
    psg: 'PSG',
    tns: 'TNS',
    fcsb: 'FCSB',
    rfs: 'RFS',
    kups: 'KuPS',
    utd: 'Utd',
    sg: 'SG',
    cp: 'CP',
    kv: 'KV',
  };
  return slug
    .split('-')
    .map((w) => ABBREV[w] || w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

function formatStartsAt(unixSec: number): string {
  const d = new Date(unixSec * 1000);
  if (Number.isNaN(d.getTime())) return '';
  const opts: Intl.DateTimeFormatOptions = {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  };
  return d.toLocaleString(undefined, opts);
}
