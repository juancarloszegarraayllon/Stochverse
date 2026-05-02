/**
 * Highlights block component.
 *
 * Renders the FlashLive /v1/events/highlights response — match
 * video clips (goals, key moments) hosted on YouTube and similar
 * platforms. Probe v2 inventory: Soccer, Cricket, Aussie Rules,
 * Rugby League ship highlights with the same 13-key shape.
 *
 * Data shape (from FL OpenAPI page 60 + probe v2):
 *   DATA: [{
 *     PROPERTY_LINK: "http://www.youtube.com/watch?v=...",
 *     PROPERTY_TITLE: "Match highlights",
 *     PROPERTY_SOURCE: "YouTube",
 *     PROPERTY_IS_TOP: 1,
 *     IMAGES: [{ HIGH: "740", PROPERTY_IMAGE_URL: "https://..." }]
 *   }]
 *
 * Hits /api/event/<ticker>/highlights directly because highlights
 * is intentionally outside /normalized fan-out for cold-start
 * latency (main.py:7244-7252). Capability-gated tab so the sub-tab
 * only appears when /capabilities.highlights === true.
 */

interface FLHighlightImage {
  HIGH?: string;
  PROPERTY_IMAGE_URL?: string;
}

interface FLHighlight {
  PROPERTY_LINK?: string;
  PROPERTY_TITLE?: string;
  PROPERTY_SOURCE?: string;
  PROPERTY_IS_TOP?: number | string;
  IMAGES?: FLHighlightImage[];
}

interface HighlightsResponse {
  data?: { DATA?: FLHighlight[] } | FLHighlight[];
  error?: string;
}

function escH(s: string | number | null | undefined): string {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function pickThumb(images: FLHighlightImage[] | undefined): string {
  if (!images || !images.length) return '';
  // FL ships multiple sizes (HIGH = pixel width). Prefer the highest
  // available resolution, fall back to the first image. Modern
  // browsers downscale cleanly so going big doesn't hurt.
  let best = images[0];
  let bestW = parseInt(String(best.HIGH || '0'), 10) || 0;
  for (let i = 1; i < images.length; i++) {
    const w = parseInt(String(images[i].HIGH || '0'), 10) || 0;
    if (w > bestW) {
      best = images[i];
      bestW = w;
    }
  }
  return best.PROPERTY_IMAGE_URL || '';
}

function renderClip(clip: FLHighlight): string {
  const link = clip.PROPERTY_LINK || '';
  const title = clip.PROPERTY_TITLE || 'Match highlights';
  const source = clip.PROPERTY_SOURCE || '';
  const thumb = pickThumb(clip.IMAGES);
  const isTop = String(clip.PROPERTY_IS_TOP || '') === '1';

  let html = '<a class="ed-hl-clip" target="_blank" rel="noopener noreferrer"';
  html += ' href="' + escH(link) + '">';
  if (thumb) {
    html += '<div class="ed-hl-thumb" style="background-image:url(\'';
    html += escH(thumb) + '\')">';
    html += '<div class="ed-hl-play">&#9654;</div>';
    html += '</div>';
  }
  html += '<div class="ed-hl-meta">';
  html += '<div class="ed-hl-title">' + escH(title) + '</div>';
  if (source || isTop) {
    html += '<div class="ed-hl-sub">';
    if (isTop) html += '<span class="ed-hl-top">Top</span>';
    if (source) html += '<span class="ed-hl-source">' + escH(source) + '</span>';
    html += '</div>';
  }
  html += '</div>';
  html += '</a>';
  return html;
}

export async function renderHighlights(
  mount: HTMLElement,
  ticker: string,
): Promise<void> {
  mount.innerHTML = '<div class="ed-stats-loading">Loading highlights…</div>';
  try {
    const r = await fetch(
      '/api/event/' + encodeURIComponent(ticker) + '/highlights',
    );
    const json = (await r.json()) as HighlightsResponse;
    if (json.error) {
      mount.innerHTML = '<div class="ed-stats-loading">' + escH(json.error) + '</div>';
      return;
    }
    let clips: FLHighlight[] = [];
    const d = json.data;
    if (d && typeof d === 'object' && !Array.isArray(d) && Array.isArray(d.DATA)) {
      clips = d.DATA as FLHighlight[];
    } else if (Array.isArray(d)) {
      clips = d as FLHighlight[];
    }
    if (!clips.length) {
      mount.innerHTML = '<div class="ed-stats-loading">No highlights available.</div>';
      return;
    }
    let html = '<div class="ed-hl-grid">';
    for (const clip of clips) html += renderClip(clip);
    html += '</div>';
    mount.innerHTML = html;
  } catch (e) {
    mount.innerHTML = '<div class="ed-stats-loading">Failed to load highlights.</div>';
  }
}
