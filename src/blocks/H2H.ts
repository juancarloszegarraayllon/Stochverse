/**
 * H2H block component.
 *
 * Self-contained: builds the sub-tab strip, renders the active tab,
 * handles per-section "Show more / Show less" toggles, and re-orders
 * tabs to match the CURRENT fixture's home/away orientation when
 * the H2H rows came from a past event with the teams flipped (see
 * yesterday's chain of patches: past-event fallback in main.py,
 * abbreviation matcher for FL TAB_NAMEs like "BAY"/"PSG", focal-
 * team mapping by display index).
 *
 * Reuses existing .h2h-* CSS classes from static/index.html so the
 * visual stays identical to the legacy renderer — no separate style
 * block.
 */
import type { H2HItem, H2HTab } from '../api/h2h';
import { fetchH2H } from '../api/h2h';

const INITIAL_SECTION_ROWS = 5;

interface ClassifiedTab {
  flIdx: number; // index into FL's response tabs[]
  label: string;
  role: 'overall' | 'home' | 'away' | 'other';
}

export async function renderH2H(
  mount: HTMLElement,
  ticker: string,
): Promise<void> {
  mount.innerHTML = '<div class="ed-stats-loading">Loading H2H…</div>';
  let resp;
  try {
    resp = await fetchH2H(ticker);
  } catch (ex) {
    const msg = ex instanceof Error ? ex.message : String(ex);
    mount.innerHTML =
      '<div class="ed-stats-loading">' +
      escHTML(msg) +
      '</div>';
    return;
  }
  const tabs = resp.data?.DATA || resp.data?.data || [];
  if (!Array.isArray(tabs) || tabs.length === 0) {
    mount.innerHTML =
      '<div class="ed-stats-loading">No H2H data available.</div>';
    return;
  }
  const homeName = resp.home_name || '';
  const awayName = resp.away_name || '';
  const displayOrder = classifyTabs(tabs, homeName, awayName);
  if (displayOrder.length === 0) {
    mount.innerHTML =
      '<div class="ed-stats-loading">No H2H tabs to show.</div>';
    return;
  }

  // Build the sub-tab strip + a single content container we re-fill
  // when the user switches tabs. Cheaper than holding all tabs'
  // rendered DOM in memory and matches the existing UX.
  mount.innerHTML = '';
  const stripWrap = document.createElement('div');
  stripWrap.className = 'ed-sb-tabs ed-sb-sub-tabs';
  const content = document.createElement('div');
  content.className = 'h2h-content';

  let activeBtn: HTMLButtonElement | null = null;
  for (let di = 0; di < displayOrder.length; di++) {
    const entry = displayOrder[di];
    const btn = document.createElement('button');
    btn.className = 'ed-sb-tab' + (di === 0 ? ' active' : '');
    btn.textContent = entry.label;
    btn.dataset.flIdx = String(entry.flIdx);
    btn.dataset.role = entry.role;
    btn.addEventListener('click', () => {
      if (activeBtn) activeBtn.classList.remove('active');
      btn.classList.add('active');
      activeBtn = btn;
      renderTabContent(
        content,
        tabs[entry.flIdx],
        entry,
        homeName,
        awayName,
      );
    });
    if (di === 0) activeBtn = btn;
    stripWrap.appendChild(btn);
  }
  mount.appendChild(stripWrap);
  mount.appendChild(content);

  // Initial render: first tab.
  const first = displayOrder[0];
  renderTabContent(content, tabs[first.flIdx], first, homeName, awayName);
}

// Map FL's tabs[] (whose TAB_NAME may carry the past matchup's home/
// away orientation) onto the current fixture. Returns a display
// order with: Overall first, then the home team's tab, then the
// away team's tab, then any leftover sport-specific tabs (tennis
// surface etc.) in their original order.
function classifyTabs(
  tabs: H2HTab[],
  homeName: string,
  awayName: string,
): ClassifiedTab[] {
  const hLow = (homeName || '').toLowerCase();
  const aLow = (awayName || '').toLowerCase();
  const hKey = hLow.split(' ')[0] || '';
  const aKey = aLow.split(' ')[0] || '';
  // First-3-char prefix fallback: FL sometimes ships TAB_NAME with
  // the broadcast short form ("BAY", "MUN", "BAR") instead of the
  // full team name. Wide enough to catch the common abbreviations,
  // narrow enough to avoid cross-team collisions in the same fixture.
  const hPrefix = (hKey || hLow).substring(0, 3);
  const aPrefix = (aKey || aLow).substring(0, 3);

  const matchesTeam = (
    rawLow: string,
    full: string,
    key: string,
    prefix: string,
  ): boolean => {
    if (!full) return false;
    if (rawLow.indexOf(full) >= 0) return true;
    if (key && rawLow.indexOf(key) >= 0) return true;
    return !!(prefix && prefix.length >= 3 && rawLow.indexOf(prefix) >= 0);
  };

  let overallIdx = -1;
  let homeIdx = -1;
  let awayIdx = -1;
  const classified = new Set<number>();
  for (let ti = 0; ti < tabs.length; ti++) {
    const raw = (tabs[ti]?.TAB_NAME || '').toLowerCase();
    if (
      overallIdx < 0 &&
      (raw === 'overall' || raw === 'all' || raw === '')
    ) {
      overallIdx = ti;
      classified.add(ti);
      continue;
    }
    const mH = matchesTeam(raw, hLow, hKey, hPrefix);
    const mA = matchesTeam(raw, aLow, aKey, aPrefix);
    if (mH && !mA && homeIdx < 0) {
      homeIdx = ti;
      classified.add(ti);
    } else if (mA && !mH && awayIdx < 0) {
      awayIdx = ti;
      classified.add(ti);
    }
  }
  // Tab 0 is reliably "Overall" in FL responses we've observed —
  // fall back when the matcher misses (empty TAB_NAME case).
  if (overallIdx < 0 && tabs.length > 0 && !classified.has(0)) {
    overallIdx = 0;
    classified.add(0);
  }

  const out: ClassifiedTab[] = [];
  if (overallIdx >= 0)
    out.push({ flIdx: overallIdx, label: 'Overall', role: 'overall' });
  if (homeIdx >= 0)
    out.push({
      flIdx: homeIdx,
      label: (homeName || 'Home') + ' - Home',
      role: 'home',
    });
  if (awayIdx >= 0)
    out.push({
      flIdx: awayIdx,
      label: (awayName || 'Away') + ' - Away',
      role: 'away',
    });
  for (let ti = 0; ti < tabs.length; ti++) {
    if (classified.has(ti)) continue;
    const raw = tabs[ti]?.TAB_NAME || `Tab ${ti + 1}`;
    out.push({ flIdx: ti, label: raw, role: 'other' });
  }
  return out;
}

interface SectionState {
  title: string;
  items: H2HItem[];
  rowsDiv: HTMLDivElement;
  expanded: boolean;
  toggleBtn: HTMLButtonElement | null;
}

function renderTabContent(
  mount: HTMLElement,
  tab: H2HTab | undefined,
  entry: ClassifiedTab,
  homeName: string,
  awayName: string,
): void {
  mount.innerHTML = '';
  if (!tab) {
    mount.innerHTML =
      '<div class="ed-stats-loading">No data for this tab.</div>';
    return;
  }
  const groups = tab.GROUPS || [];
  if (groups.length === 0) {
    mount.innerHTML =
      '<div class="ed-stats-loading">No H2H data available.</div>';
    return;
  }
  const sections: Array<{ title: string; items: H2HItem[] }> = [];
  for (const grp of groups) {
    const items = grp.ITEMS || [];
    if (items.length > 0) {
      sections.push({
        title: grp.GROUP_LABEL || grp.NAME || '',
        items,
      });
    }
  }
  if (sections.length === 0) {
    mount.innerHTML =
      '<div class="ed-stats-loading">No matches found.</div>';
    return;
  }

  // Focal team for the win/loss/draw badge perspective. For the
  // home tab, the focal team is the fixture's home team; same for
  // away. Overall has no focal — H2H rows in that tab render
  // without badges, matching FL's UX.
  const focalTeam =
    entry.role === 'home'
      ? homeName
      : entry.role === 'away'
        ? awayName
        : '';

  for (let si = 0; si < sections.length; si++) {
    const sec = sections[si];
    let secTitle = sec.title;
    if (!secTitle) {
      // Synthesize section labels when FL omits them. Section
      // ordering inside each tab:
      //   Overall → [home_matches, away_matches, h2h]
      //   Home    → [home_matches, h2h]
      //   Away    → [away_matches, h2h]
      const firstFocal = entry.role === 'away' ? awayName : homeName;
      if (si === 0) secTitle = 'Last matches: ' + firstFocal;
      else if (si === 1 && entry.role === 'overall')
        secTitle = 'Last matches: ' + awayName;
      else secTitle = 'Head-to-head matches';
    }
    const sectionEl = document.createElement('div');
    sectionEl.className = 'h2h-section';

    const titleEl = document.createElement('div');
    titleEl.className = 'h2h-section-title';
    titleEl.textContent = secTitle;
    sectionEl.appendChild(titleEl);

    const rowsDiv = document.createElement('div');
    rowsDiv.className = 'h2h-rows';
    sectionEl.appendChild(rowsDiv);

    const initialMax = Math.min(sec.items.length, INITIAL_SECTION_ROWS);
    for (let mi = 0; mi < initialMax; mi++) {
      rowsDiv.insertAdjacentHTML(
        'beforeend',
        renderH2HRow(sec.items[mi], homeName, awayName, focalTeam),
      );
    }

    if (sec.items.length > INITIAL_SECTION_ROWS) {
      const wrap = document.createElement('div');
      wrap.className = 'h2h-show-more-wrap';
      const btn = document.createElement('button');
      btn.className = 'h2h-show-more';
      btn.textContent = 'Show more matches';
      const state: SectionState = {
        title: secTitle,
        items: sec.items,
        rowsDiv,
        expanded: false,
        toggleBtn: btn,
      };
      btn.addEventListener('click', () => toggleSection(state, sectionEl, homeName, awayName, focalTeam));
      wrap.appendChild(btn);
      sectionEl.appendChild(wrap);
    }
    mount.appendChild(sectionEl);
  }
}

function toggleSection(
  state: SectionState,
  sectionEl: HTMLElement,
  homeName: string,
  awayName: string,
  focalTeam: string,
): void {
  state.expanded = !state.expanded;
  const limit = state.expanded
    ? state.items.length
    : Math.min(state.items.length, INITIAL_SECTION_ROWS);
  let html = '';
  for (let i = 0; i < limit; i++) {
    html += renderH2HRow(state.items[i], homeName, awayName, focalTeam);
  }
  state.rowsDiv.innerHTML = html;
  if (state.toggleBtn) {
    state.toggleBtn.textContent = state.expanded
      ? 'Show less'
      : 'Show more matches';
  }
  // On collapse, scroll the section header back into view so the
  // user isn't stranded past where the expanded list ended.
  if (!state.expanded && sectionEl.scrollIntoView) {
    sectionEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// Mirrors _renderH2HRow in static/index.html. Returns an HTML
// string we insertAdjacentHTML into the rows container — same
// pattern the legacy renderer uses, same .h2h-* class names so
// the existing CSS applies directly.
function renderH2HRow(
  m: H2HItem,
  homeName: string,
  awayName: string,
  focalTeam: string,
): string {
  // Strip leading "*" FL uses to mark the actual home team — the
  // field naming already conveys that, asterisk is noise.
  const home = (m.HOME_PARTICIPANT_NAME_ONE || m.HOME_PARTICIPANT || '')
    .replace(/^\*/, '');
  const away = (m.AWAY_PARTICIPANT_NAME_ONE || m.AWAY_PARTICIPANT || '')
    .replace(/^\*/, '');
  const hs = m.HOME_SCORE_FULL != null ? String(m.HOME_SCORE_FULL) : '';
  const as2 = m.AWAY_SCORE_FULL != null ? String(m.AWAY_SCORE_FULL) : '';
  let result = (m.H_RESULT || '').toUpperCase();
  // FL populates H_RESULT for "Last matches" sections (one focal
  // team per row) but leaves it empty in the head-to-head section.
  // Derive from score relative to the tab's focal team.
  if (!result && focalTeam) {
    const fhs = parseInt(hs, 10);
    const fas = parseInt(as2, 10);
    if (!isNaN(fhs) && !isNaN(fas)) {
      const focalLow = focalTeam.toLowerCase();
      const focalIsHome = home.toLowerCase().indexOf(focalLow) >= 0;
      const focalIsAway = away.toLowerCase().indexOf(focalLow) >= 0;
      if (focalIsHome || focalIsAway) {
        if (fhs === fas) result = 'DRAW';
        else if (
          (focalIsHome && fhs > fas) ||
          (focalIsAway && fas > fhs)
        )
          result = 'WIN';
        else result = 'LOSS';
      }
    }
  }
  const league = m.EVENT_NAME || '';
  // EVENT_ACRONYM is FL's compact tournament code (LPF, COP, CL,
  // CF, PN, etc.). Always 2-4 chars, fits the league cell.
  const acronym = m.EVENT_ACRONYM || '';
  const leagueLabel =
    acronym ||
    (league.length > 6 ? league.substring(0, 5) + '…' : league);
  // STAGE = AFTER_PENALTIES / AFTER_EXTRA_TIME → (P) / (AET) suffix.
  const stage = (m.STAGE || '').toUpperCase();
  let stageSuffix = '';
  if (stage === 'AFTER_PENALTIES' || stage === 'AP')
    stageSuffix = ' <span class="h2h-stage">(P)</span>';
  else if (stage === 'AFTER_EXTRA_TIME' || stage === 'AET')
    stageSuffix = ' <span class="h2h-stage">(AET)</span>';
  const homeImg = (m.HOME_IMAGES && m.HOME_IMAGES[0]) || '';
  const awayImg = (m.AWAY_IMAGES && m.AWAY_IMAGES[0]) || '';
  let dateStr = '';
  if (m.START_TIME) {
    try {
      const dt = new Date(m.START_TIME * 1000);
      dateStr =
        ('0' + dt.getDate()).slice(-2) +
        '.' +
        ('0' + (dt.getMonth() + 1)).slice(-2) +
        '.' +
        String(dt.getFullYear()).slice(-2);
    } catch {
      /* fallthrough */
    }
  }
  const homeHL = !!(homeName &&
    home.toLowerCase().indexOf(homeName.toLowerCase()) >= 0);
  const awayHL = !!(awayName &&
    away.toLowerCase().indexOf(awayName.toLowerCase()) >= 0);
  const hsNum = parseInt(hs, 10);
  const asNum = parseInt(as2, 10);
  const homeWon = !isNaN(hsNum) && !isNaN(asNum) && hsNum > asNum;
  const awayWon = !isNaN(hsNum) && !isNaN(asNum) && asNum > hsNum;
  let badgeHTML = '';
  if (result === 'WIN') badgeHTML = '<span class="h2h-badge h2h-win">W</span>';
  else if (result === 'LOSS' || result === 'LOST')
    badgeHTML = '<span class="h2h-badge h2h-loss">L</span>';
  else if (result === 'DRAW')
    badgeHTML = '<span class="h2h-badge h2h-draw">D</span>';

  let h = '<div class="h2h-row">';
  h += '<span class="h2h-date">' + escHTML(dateStr) + '</span>';
  h +=
    '<span class="h2h-league" title="' +
    escHTML(league) +
    '">' +
    escHTML(leagueLabel) +
    '</span>';
  h +=
    '<span class="h2h-team h2h-home' +
    (homeHL ? ' h2h-hl' : '') +
    '">';
  h +=
    '<span class="h2h-name' +
    (homeWon ? ' h2h-winner' : '') +
    '">' +
    escHTML(home) +
    '</span>';
  if (homeImg)
    h +=
      '<img class="h2h-crest" src="' +
      escHTML(homeImg) +
      '" alt="" loading="lazy">';
  h += '</span>';
  h +=
    '<span class="h2h-score">' +
    escHTML(hs) +
    '-' +
    escHTML(as2) +
    stageSuffix +
    '</span>';
  h +=
    '<span class="h2h-team h2h-away' +
    (awayHL ? ' h2h-hl' : '') +
    '">';
  if (awayImg)
    h +=
      '<img class="h2h-crest" src="' +
      escHTML(awayImg) +
      '" alt="" loading="lazy">';
  h +=
    '<span class="h2h-name' +
    (awayWon ? ' h2h-winner' : '') +
    '">' +
    escHTML(away) +
    '</span>';
  h += '</span>';
  h += badgeHTML;
  h += '</div>';
  return h;
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
