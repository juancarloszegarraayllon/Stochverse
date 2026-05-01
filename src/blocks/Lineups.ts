/**
 * Lineups block component.
 *
 * Renders `data.lineups` from /normalized. Data shape (parsed by
 * _parse_flashlive_lineups in main.py):
 *
 *   {home: {players, substitutes, formation, manager, coaches},
 *    away: {…same…}}
 *
 *   player = {jerseyNumber, name, position, rating, incidents,
 *             captain, rrank, substitute}
 *   incident codes: 1=yellow, 2=red, 3=goal, 6=sub-out, 7=sub-in,
 *                   8=assist
 *
 * Reuses .ed-lu-* CSS classes from static/index.html so the visual
 * is identical to the legacy renderer — no separate style block.
 *
 * Sport-aware (future-proofing): this is the first block whose
 * layout will diverge by sport. Soccer gets formation + Starting
 * XI / Substitutes / Coach sections. Other sports (basketball /
 * hockey) ship a flat roster without formation. For now the block
 * defaults to the soccer layout because that's the only sport for
 * which FL ships lineups we surface; if/when we add lineup support
 * for other sports, the sport-aware branch lands here as a small
 * switch on the sport arg.
 */

interface LineupPlayer {
  jerseyNumber?: number | string;
  name?: string;
  position?: string;
  rating?: number | string;
  incidents?: number[];
  captain?: boolean;
  rrank?: number | string;
  substitute?: boolean;
}

interface LineupSide {
  players?: LineupPlayer[];
  substitutes?: LineupPlayer[];
  coaches?: LineupPlayer[];
  formation?: string;
  manager?: string;
}

interface LineupsData {
  home?: LineupSide;
  away?: LineupSide;
}

export function renderLineups(
  mount: HTMLElement,
  data: LineupsData | null | undefined,
  homeName: string,
  awayName: string,
): void {
  mount.innerHTML = '';
  const hasHome = !!data?.home && !!data.home.players?.length;
  const hasAway = !!data?.away && !!data.away.players?.length;
  if (!data || (!hasHome && !hasAway)) {
    const empty = document.createElement('div');
    empty.className = 'ed-stats-loading';
    empty.textContent = 'Lineups not published yet.';
    mount.appendChild(empty);
    return;
  }

  let html = '';
  for (const side of ['home', 'away'] as const) {
    const lu = (data[side] || {}) as LineupSide;
    const teamName = side === 'home' ? homeName : awayName;
    html += renderSide(lu, teamName);
  }
  mount.innerHTML = html;
}

function renderSide(lu: LineupSide, teamName: string): string {
  let h = '';
  h += `<div class="ed-stats-title">${escHTML(teamName || '')}</div>`;
  if (lu.formation) {
    h += `<div class="ed-lu-formation">${escHTML(lu.formation)}</div>`;
  }
  if (lu.manager) {
    h +=
      '<div class="ed-lu-manager"><span class="ed-lu-manager-label">Manager:</span> ' +
      escHTML(lu.manager) +
      '</div>';
  }

  const players = lu.players || [];
  const starters = players.filter((p) => !p.substitute);
  const subs =
    lu.substitutes && lu.substitutes.length > 0
      ? lu.substitutes
      : players.filter((p) => p.substitute);

  if (starters.length > 0) {
    h += '<div class="ed-lu-section">Starting XI</div>';
    for (const p of starters) h += renderPlayer(p);
  }
  if (subs.length > 0) {
    h += '<div class="ed-lu-section">Substitutes</div>';
    for (const p of subs) h += renderPlayer(p);
  }
  if (lu.coaches && lu.coaches.length > 0) {
    h += '<div class="ed-lu-section">Coach</div>';
    for (const c of lu.coaches) h += renderPlayer(c, { showPos: false });
  }
  return h;
}

function renderPlayer(
  p: LineupPlayer,
  opts: { showPos?: boolean } = {},
): string {
  const showPos = opts.showPos !== false;
  const ratingNum = parseFloat(String(p.rating ?? ''));
  const hasRating = !isNaN(ratingNum) && ratingNum > 0;
  // FlashScore tier thresholds: 7.5+ green ("hi"), 6.5+ neutral
  // ("mid"), below red ("lo").
  const ratingClass = hasRating
    ? ratingNum >= 7.5
      ? 'ed-lu-rating ed-lu-rating-hi'
      : ratingNum >= 6.5
        ? 'ed-lu-rating ed-lu-rating-mid'
        : 'ed-lu-rating ed-lu-rating-lo'
    : '';

  let incidentsHTML = '';
  if (Array.isArray(p.incidents)) {
    for (const code of p.incidents) incidentsHTML += incidentIcon(code);
  }

  // FL's LRR=1 marks the top performer (Player of the Match).
  const rrankBadge =
    p.rrank === '1' || p.rrank === 1
      ? '<span class="ed-lu-rrank" title="Top performer">★</span>'
      : '';

  let h = '<div class="ed-lu-player">';
  h +=
    '<div class="ed-lu-num">' +
    (p.jerseyNumber != null ? String(p.jerseyNumber) : '') +
    '</div>';
  h += '<div class="ed-lu-name">';
  h += escHTML(p.name || '');
  if (p.captain) h += ' <span class="ed-lu-captain">(C)</span>';
  h += rrankBadge;
  if (incidentsHTML) h += ' ' + incidentsHTML;
  h += '</div>';
  if (showPos) {
    h += '<div class="ed-lu-pos">' + escHTML(p.position || '') + '</div>';
  }
  if (hasRating) {
    h +=
      '<div class="' +
      ratingClass +
      '">' +
      ratingNum.toFixed(1) +
      '</div>';
  }
  h += '</div>';
  return h;
}

function incidentIcon(code: number): string {
  switch (code) {
    case 1:
      return '<span class="ed-lu-inc ed-lu-inc-yc" title="Yellow card">▢</span>';
    case 2:
      return '<span class="ed-lu-inc ed-lu-inc-rc" title="Red card">▢</span>';
    case 3:
      return '<span class="ed-lu-inc ed-lu-inc-goal" title="Goal">⚽</span>';
    case 6:
      return '<span class="ed-lu-inc ed-lu-inc-out" title="Subbed off">▼</span>';
    case 7:
      return '<span class="ed-lu-inc ed-lu-inc-in" title="Subbed on">▲</span>';
    case 8:
      return '<span class="ed-lu-inc ed-lu-inc-assist" title="Assist">A</span>';
    default:
      return '';
  }
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
