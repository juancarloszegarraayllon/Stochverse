/**
 * Commentary block component.
 *
 * Self-contained: fetches /api/event/<t>/commentary, renders the
 * timeline, and runs its own 15s auto-poll while the mount is in
 * the DOM. Stops polling automatically when the mount disappears
 * (tab switch removes the panel from view but not the DOM, so we
 * also expose a stop hook the inline JS calls on tab change).
 *
 * Why /commentary not /normalized: commentary updates every few
 * seconds during a live match. /normalized has a 5-min cache, so
 * pulling commentary through it would freeze the timeline. The
 * dedicated endpoint is uncached on the backend.
 */

const POLL_INTERVAL_MS = 15_000;

interface CommentaryItem {
  COMMENT_TIME?: string | number;
  TIME?: string | number;
  INCIDENT_TIME?: string | number;
  COMMENT_TEXT?: string;
  COMMENT?: string;
  TEXT?: string;
  BODY?: string;
  COMMENT_IS_IMPORTANT?: boolean | number | string;
  IS_IMPORTANT?: boolean | number | string;
  ITEMS?: CommentaryItem[];
}

// Track active polls per mount so we don't double-poll if the
// caller re-invokes renderCommentary on the same element.
const activePolls = new WeakMap<HTMLElement, number>();

export async function renderCommentary(
  mount: HTMLElement,
  ticker: string,
): Promise<void> {
  // Clear any prior poll on this mount before starting a new one.
  stopCommentaryPoll(mount);

  mount.innerHTML =
    '<div class="ed-stats-loading">Loading commentary…</div>';
  await fetchAndRender(mount, ticker);

  // Start auto-poll. The interval runs as long as the mount is
  // attached; once the user switches tabs the inline switchMatchTab
  // calls stopCommentaryPoll(), which clears the timer here.
  const id = window.setInterval(() => {
    if (!document.body.contains(mount)) {
      stopCommentaryPoll(mount);
      return;
    }
    fetchAndRender(mount, ticker);
  }, POLL_INTERVAL_MS);
  activePolls.set(mount, id);
}

export function stopCommentaryPoll(mount: HTMLElement): void {
  const id = activePolls.get(mount);
  if (id) {
    window.clearInterval(id);
    activePolls.delete(mount);
  }
}

async function fetchAndRender(
  mount: HTMLElement,
  ticker: string,
): Promise<void> {
  try {
    const res = await fetch(
      `/api/event/${encodeURIComponent(ticker)}/commentary`,
    );
    const d = await res.json();
    if (d && d.error) {
      mount.innerHTML =
        '<div class="ed-stats-loading">' +
        escHTML(String(d.error)) +
        '</div>';
      stopCommentaryPoll(mount);
      return;
    }
    const data = d?.data || {};
    let items: CommentaryItem[] = data.DATA || data.data || [];
    if (!Array.isArray(items)) items = [];
    // FL nests comments either flat or under stage.ITEMS — flatten.
    const comments: CommentaryItem[] = [];
    for (const item of items) {
      if (item.ITEMS && Array.isArray(item.ITEMS)) {
        for (const inner of item.ITEMS) comments.push(inner);
      } else {
        comments.push(item);
      }
    }
    const valid = comments.filter((c) =>
      String(c.COMMENT_TEXT || c.COMMENT || c.TEXT || c.BODY || ''),
    );
    if (valid.length === 0) {
      mount.innerHTML =
        '<div class="ed-stats-loading">No commentary available.</div>';
      return;
    }

    // Render. Reuse legacy inline visual: minute label + text per row,
    // important comments in full text color, others dimmed. Inline
    // styles match the legacy renderer exactly so the panel doesn't
    // visually shift when this block takes over from the inline path.
    let h = '<div style="font-size:11px">';
    for (const c of valid) {
      const minute = c.COMMENT_TIME || c.TIME || c.INCIDENT_TIME || '';
      const text =
        c.COMMENT_TEXT || c.COMMENT || c.TEXT || c.BODY || '';
      const isImportant = !!(c.COMMENT_IS_IMPORTANT || c.IS_IMPORTANT);
      h +=
        '<div style="padding:8px 0;border-bottom:1px solid var(--border)">';
      h +=
        '<div style="font-weight:700;color:var(--text);margin-bottom:3px">' +
        escHTML(String(minute)) +
        (minute ? "'" : '') +
        '</div>';
      h +=
        '<div style="color:' +
        (isImportant ? 'var(--text)' : 'var(--text-dim)') +
        ';line-height:1.4">' +
        escHTML(text) +
        '</div>';
      h += '</div>';
    }
    h += '</div>';
    mount.innerHTML = h;
  } catch {
    mount.innerHTML =
      '<div class="ed-stats-loading">Failed to load commentary.</div>';
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
