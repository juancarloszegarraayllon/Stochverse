/**
 * Summary / Timeline block component.
 *
 * Renders `data.incidents` from /normalized — pre-parsed by the
 * backend's _parse_flashlive_incidents (and a parallel SofaScore
 * shape for sports where SS is the primary). Each item is one of:
 *
 *   {time, type, side, isHome, player, assist, playerIn, playerOut,
 *    homeScore, awayScore, addedTime, goalType, incidentClass,
 *    icon, text}
 *
 * Layout matches the legacy inline renderer: minute · icon · detail,
 * left-aligned (home), right-aligned (away), or centered (period /
 * injury time). Reuses .ed-tl-* CSS classes from static/index.html
 * so the visual is identical and no separate stylesheet is needed.
 *
 * Soccer is the only sport that ships incidents today (FL doesn't
 * push timelines for tennis/cricket/basketball). When the array is
 * empty the block prints the same "No match events yet." placeholder
 * the inline path used.
 */
import type { NormalizedEvent } from '../types/normalized';
import { fetchNormalized } from '../api/normalized';

interface Incident {
  time?: string | number;
  type?: string;
  side?: 'home' | 'away' | 'neutral' | string;
  isHome?: boolean | null;
  player?: string;
  assist?: string;
  playerIn?: string;
  playerOut?: string;
  homeScore?: number | string | null;
  awayScore?: number | string | null;
  addedTime?: number | string;
  goalType?: string;
  incidentClass?: string;
  icon?: string;
  text?: string;
  length?: number | string;
}

export async function renderSummary(
  mount: HTMLElement,
  ticker: string,
): Promise<void> {
  mount.innerHTML =
    '<div class="ed-stats-loading">Loading timeline…</div>';
  try {
    const ev: NormalizedEvent = await fetchNormalized(ticker);
    let incidents = ((ev.data as { incidents?: Incident[] }).incidents ||
      []) as Incident[];
    // Fallback to dedicated /stats endpoint when /normalized's
    // summary_incidents probe came back empty (parallel fan-out
    // individual probe failure under FL pressure). /stats parses
    // the same FL data into the same shape, so no shape adapter
    // needed.
    if (incidents.length === 0) {
      try {
        const r = await fetch(
          '/api/event/' + encodeURIComponent(ticker) + '/stats',
        );
        if (r.ok) {
          const d = await r.json();
          if (d && !d.error && Array.isArray(d.incidents)) {
            incidents = d.incidents as Incident[];
          }
        }
      } catch {
        /* keeps the empty-state below */
      }
    }
    renderInto(mount, incidents);
  } catch {
    mount.innerHTML =
      '<div class="ed-stats-loading">Timeline failed to load.</div>';
  }
}

function renderInto(mount: HTMLElement, incidents: Incident[]): void {
  if (!incidents || incidents.length === 0) {
    mount.innerHTML =
      '<div class="ed-stats-loading">No match events yet.</div>';
    return;
  }
  let h = '<div class="ed-stats-title" style="margin-top:16px">Timeline</div>';
  for (const inc of incidents) {
    const itype = inc.type || '';
    const itypeLower = itype.toLowerCase();
    let minute =
      inc.time != null && inc.time !== '' ? String(inc.time) + "'" : '';
    if (inc.addedTime && inc.addedTime !== 999) {
      minute += '+' + inc.addedTime;
    }
    let sideCls: string;
    if (inc.side === 'home' || inc.isHome === true) sideCls = ' ed-tl-home';
    else if (inc.side === 'away' || inc.isHome === false)
      sideCls = ' ed-tl-away';
    else sideCls = ' ed-tl-neutral';

    let icon = inc.icon || '';
    let detail = '';

    if (itypeLower === 'goal' || itype === 'Goal') {
      icon = '⚽';
      detail =
        '<span class="ed-tl-player">' + escHTML(inc.player || '') + '</span>';
      if (inc.assist) {
        detail +=
          '<br><span class="ed-tl-assist">Assist: ' +
          escHTML(inc.assist) +
          '</span>';
      }
      if (inc.homeScore != null && inc.awayScore != null) {
        detail +=
          '<span class="ed-tl-score">' +
          inc.homeScore +
          '-' +
          inc.awayScore +
          '</span>';
      }
      if (inc.goalType === 'ownGoal') {
        detail += ' <span class="ed-tl-assist">(OG)</span>';
      }
      if (inc.goalType === 'penalty') {
        detail += ' <span class="ed-tl-assist">(Pen)</span>';
      }
    } else if (
      itypeLower === 'card' ||
      itypeLower === 'yellow card' ||
      itypeLower === 'red card'
    ) {
      icon =
        inc.incidentClass === 'red' ? '🟥' : '🟨';
      detail =
        '<span class="ed-tl-player">' + escHTML(inc.player || '') + '</span>';
    } else if (itypeLower === 'substitution') {
      icon = '🔄';
      detail =
        '<span class="ed-tl-sub-in">▲ ' +
        escHTML(inc.playerIn || '') +
        '</span>';
      detail +=
        '<br><span class="ed-tl-sub-out">▼ ' +
        escHTML(inc.playerOut || '') +
        '</span>';
    } else if (itypeLower === 'period') {
      icon = '⏱';
      detail =
        '<span class="ed-tl-player">' +
        escHTML(inc.text || 'Period') +
        '</span>';
      sideCls = ' ed-tl-neutral';
    } else if (itypeLower === 'injurytime') {
      icon = '⏳';
      detail =
        '<span class="ed-tl-assist">+' +
        escHTML(String(inc.length || inc.text || '')) +
        ' min added</span>';
      sideCls = ' ed-tl-neutral';
    } else if (itypeLower === 'vardecision') {
      icon = '📺';
      detail =
        '<span class="ed-tl-player">VAR: ' +
        escHTML(inc.text || inc.incidentClass || '') +
        '</span>';
    } else if (itypeLower === 'ingamepenalty' || itypeLower === 'penalty') {
      icon = '⚽';
      detail =
        '<span class="ed-tl-player">' +
        escHTML(inc.player || 'Penalty') +
        '</span>';
      if (inc.text) {
        detail +=
          '<br><span class="ed-tl-assist">' + escHTML(inc.text) + '</span>';
      }
    } else {
      // Unknown type — surface it rather than silently dropping it.
      icon = '•';
      const fallback = inc.player || inc.text || inc.incidentClass || itype;
      if (!fallback) continue;
      detail =
        '<span class="ed-tl-player">' + escHTML(String(fallback)) + '</span>';
    }
    h += '<div class="ed-tl-item' + sideCls + '">';
    h += '<div class="ed-tl-time">' + escHTML(minute) + '</div>';
    h += '<div class="ed-tl-icon">' + icon + '</div>';
    h += '<div class="ed-tl-detail">' + detail + '</div>';
    h += '</div>';
  }
  mount.innerHTML = h;
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
