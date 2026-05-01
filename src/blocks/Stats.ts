/**
 * Stats block component.
 *
 * Renders `data.stats` from /normalized — FL's per-event statistics
 * already parsed into the shape:
 *
 *   { home: "Bayern Munich", away: "PSG", sport: "Soccer",
 *     stats: [{name, home, away}, ...]            (Match-stage flat list)
 *     stats_grouped: [
 *       {name: "Match" | "1st Half" | …,
 *        groups: [{label: "Top stats" | "Shots" | …,
 *                  items: [{name, home, away}, …]}]}
 *     ]}
 *
 * Layout mirrors the legacy inline renderer in static/index.html:
 *  - team-name header row (home left, away right)
 *  - stage sub-tab strip across the top (Match / 1st Half / 2nd Half)
 *  - per-group section header + stat rows
 *  - each stat row: home value, stat name (centered), away value, bar
 *
 * Falls back to the flat `stats` list when stats_grouped is empty
 * (older parses, sports without stage breakdowns).
 */

const STYLE_ID = 'sv-stats-styles';
const STYLES = `
.sv-stats{display:flex;flex-direction:column;gap:6px;padding:8px 4px}
.sv-stats-teams{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:6px 0;border-bottom:1px solid var(--border,#1a1a1a);margin-bottom:6px}
.sv-stats-team{font-weight:600;font-size:13px}
.sv-stats-team:nth-child(1){text-align:left}
.sv-stats-team:nth-child(2){text-align:right}
.sv-stats-stage-tabs{display:flex;gap:2px;border-bottom:1px solid var(--border,#1a1a1a);margin-bottom:8px;overflow-x:auto;scrollbar-width:thin}
.sv-stats-stage-tab{flex:0 0 auto;padding:6px 10px;background:transparent;border:none;color:var(--text-dim,#888);font-family:inherit;font-size:11px;font-weight:600;letter-spacing:.3px;text-transform:uppercase;cursor:pointer;border-bottom:2px solid transparent}
.sv-stats-stage-tab.active{color:var(--green,#00ff00);border-bottom-color:var(--green,#00ff00)}
.sv-stats-stage-tab:hover:not(.active){color:var(--text,#fff)}
.sv-stats-group-label{font-size:10px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:var(--text-dim,#888);padding:8px 0 4px;border-bottom:1px solid var(--border,#1a1a1a)}
.sv-stats-row{padding:6px 0;border-bottom:1px solid var(--border,#1a1a1a)}
.sv-stats-row:last-child{border-bottom:0}
.sv-stats-row-cells{display:grid;grid-template-columns:1fr 2fr 1fr;align-items:center;font-size:13px;gap:8px}
.sv-stats-home{text-align:left;font-weight:600;font-variant-numeric:tabular-nums}
.sv-stats-name{text-align:center;color:var(--text-dim,#888);font-size:11px;text-transform:uppercase;letter-spacing:.3px}
.sv-stats-away{text-align:right;font-weight:600;font-variant-numeric:tabular-nums}
.sv-stats-bar{display:flex;height:3px;margin-top:4px;background:var(--border,#1a1a1a);border-radius:2px;overflow:hidden}
.sv-stats-bar-home{background:var(--green,#00ff00);height:100%}
.sv-stats-bar-away{background:#888;height:100%;margin-left:auto}
.sv-stats-empty{color:var(--text-dim,#888);font-size:13px;padding:20px;text-align:center}
`;

function ensureStyles(): void {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = STYLES;
  document.head.appendChild(style);
}

interface StatRow {
  name: string;
  home: string;
  away: string;
}
interface StatGroup {
  label: string;
  items: StatRow[];
}
interface StatStage {
  name: string;
  groups: StatGroup[];
}
interface StatsData {
  home: string;
  away: string;
  sport: string;
  stats?: StatRow[];
  stats_grouped?: StatStage[];
}

export function renderStats(
  mount: HTMLElement,
  data: StatsData | null | undefined,
): void {
  ensureStyles();
  mount.innerHTML = '';
  const stages = data?.stats_grouped || [];
  const flatStats = data?.stats || [];
  if (!data || (stages.length === 0 && flatStats.length === 0)) {
    const empty = document.createElement('div');
    empty.className = 'sv-stats-empty';
    empty.textContent = 'No stats available yet.';
    mount.appendChild(empty);
    return;
  }

  const wrap = document.createElement('div');
  wrap.className = 'sv-stats';

  // Team header row.
  const teams = document.createElement('div');
  teams.className = 'sv-stats-teams';
  const home = document.createElement('div');
  home.className = 'sv-stats-team';
  home.textContent = data.home || 'Home';
  const away = document.createElement('div');
  away.className = 'sv-stats-team';
  away.textContent = data.away || 'Away';
  teams.appendChild(home);
  teams.appendChild(away);
  wrap.appendChild(teams);

  if (stages.length > 0) {
    // Stage sub-tab strip.
    const tabs = document.createElement('div');
    tabs.className = 'sv-stats-stage-tabs';
    const panels: HTMLElement[] = [];
    for (let i = 0; i < stages.length; i++) {
      const s = stages[i];
      const btn = document.createElement('button');
      btn.className = 'sv-stats-stage-tab' + (i === 0 ? ' active' : '');
      btn.textContent = s.name || `Stage ${i + 1}`;
      btn.dataset.stageIdx = String(i);
      btn.addEventListener('click', () => {
        for (let j = 0; j < tabs.children.length; j++) {
          tabs.children[j].classList.toggle(
            'active',
            j === i,
          );
        }
        for (let j = 0; j < panels.length; j++) {
          panels[j].style.display = j === i ? 'block' : 'none';
        }
      });
      tabs.appendChild(btn);
    }
    wrap.appendChild(tabs);

    for (let i = 0; i < stages.length; i++) {
      const panel = document.createElement('div');
      panel.style.display = i === 0 ? 'block' : 'none';
      panel.dataset.stageIdx = String(i);
      for (const grp of stages[i].groups) {
        if (grp.label) {
          const lbl = document.createElement('div');
          lbl.className = 'sv-stats-group-label';
          lbl.textContent = grp.label;
          panel.appendChild(lbl);
        }
        for (const item of grp.items) {
          panel.appendChild(renderRow(item));
        }
      }
      panels.push(panel);
      wrap.appendChild(panel);
    }
  } else {
    // No stage breakdown — render the flat list (older parses or
    // sports that don't ship per-period stats).
    for (const item of flatStats) {
      wrap.appendChild(renderRow(item));
    }
  }

  mount.appendChild(wrap);
}

function renderRow(s: StatRow): HTMLElement {
  const row = document.createElement('div');
  row.className = 'sv-stats-row';
  const cells = document.createElement('div');
  cells.className = 'sv-stats-row-cells';
  const homeEl = document.createElement('div');
  homeEl.className = 'sv-stats-home';
  homeEl.textContent = s.home || '';
  const nameEl = document.createElement('div');
  nameEl.className = 'sv-stats-name';
  nameEl.textContent = s.name || '';
  const awayEl = document.createElement('div');
  awayEl.className = 'sv-stats-away';
  awayEl.textContent = s.away || '';
  cells.appendChild(homeEl);
  cells.appendChild(nameEl);
  cells.appendChild(awayEl);
  row.appendChild(cells);

  // Bar visualization for numeric stats. Strip trailing %, parse,
  // skip rendering when both sides are zero or non-numeric.
  const hNum = parseFloat(String(s.home).replace('%', ''));
  const aNum = parseFloat(String(s.away).replace('%', ''));
  if (!isNaN(hNum) && !isNaN(aNum) && hNum + aNum > 0) {
    const total = hNum + aNum;
    const hPct = (hNum / total) * 100;
    const bar = document.createElement('div');
    bar.className = 'sv-stats-bar';
    const hBar = document.createElement('div');
    hBar.className = 'sv-stats-bar-home';
    hBar.style.width = hPct.toFixed(1) + '%';
    const aBar = document.createElement('div');
    aBar.className = 'sv-stats-bar-away';
    aBar.style.width = (100 - hPct).toFixed(1) + '%';
    bar.appendChild(hBar);
    bar.appendChild(aBar);
    row.appendChild(bar);
  }
  return row;
}
