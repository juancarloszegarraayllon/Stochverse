/**
 * News block component.
 *
 * Renders `data.news` from /normalized — FL's per-event news feed.
 * Going through /normalized (not /api/event/<t>/news directly) lets
 * us share the 5-min cache with every other block on the panel and
 * picks up any future-fixture fallback the backend grows. News
 * doesn't refresh more than once or twice a day, so the cache TTL
 * isn't a problem here (unlike Commentary).
 *
 * Visual matches the legacy inline renderer exactly: thumbnail (60×45)
 * + title link + source · date footer, capped at 10 articles.
 */
import type { NormalizedEvent } from '../types/normalized';
import { fetchNormalized } from '../api/normalized';

interface NewsArticleLink {
  IMAGE_VARIANT_URL?: string;
}

interface NewsArticle {
  TITLE?: string;
  title?: string;
  LINK?: string;
  link?: string;
  PUBLISHED?: number | string;
  PUBLISHED_AT?: number | string;
  published_at?: number | string;
  published?: number | string;
  PROVIDER_NAME?: string;
  SOURCE_NAME?: string;
  source_name?: string;
  LINKS?: NewsArticleLink[];
}

const MAX_ARTICLES = 10;

export async function renderNews(
  mount: HTMLElement,
  ticker: string,
): Promise<void> {
  mount.innerHTML = '<div class="ed-stats-loading">Loading news…</div>';
  try {
    const ev: NormalizedEvent = await fetchNormalized(ticker);
    let articles = extractArticles((ev.data as { news?: unknown }).news);
    // Fallback: when /normalized's news probe came back empty
    // (failed during the parallel fan-out under FL pressure),
    // hit the dedicated endpoint. One probe instead of one of N
    // — usually succeeds where the parallel fetch didn't.
    if (articles.length === 0) {
      try {
        const dedicated = await fetch(
          '/api/event/' + encodeURIComponent(ticker) + '/news',
        );
        if (dedicated.ok) {
          const d = await dedicated.json();
          if (d && !d.error) {
            articles = extractArticles(d.data);
          }
        }
      } catch {
        /* ignore — keeps the empty-state below */
      }
    }
    if (articles.length === 0) {
      mount.innerHTML =
        '<div class="ed-stats-loading">No news available.</div>';
      return;
    }

    let h = '';
    for (const a of articles.slice(0, MAX_ARTICLES)) {
      if (!a || typeof a !== 'object') continue;
      const title = a.TITLE || a.title || '';
      const link = a.LINK || a.link || '';
      let published = '';
      const pubTs =
        a.PUBLISHED || a.PUBLISHED_AT || a.published_at || a.published;
      if (pubTs) {
        try {
          published = new Date(Number(pubTs) * 1000).toLocaleDateString(
            'en-US',
            { month: 'short', day: 'numeric', year: 'numeric' },
          );
        } catch {
          /* ignore date parse errors */
        }
      }
      const source = a.PROVIDER_NAME || a.SOURCE_NAME || a.source_name || '';
      let imgUrl = '';
      if (a.LINKS && a.LINKS.length > 0) {
        imgUrl = a.LINKS[0].IMAGE_VARIANT_URL || '';
      }
      h +=
        '<div style="padding:10px 0;border-bottom:1px solid var(--border);display:flex;gap:10px">';
      if (imgUrl) {
        h +=
          '<img src="' +
          escHTML(imgUrl) +
          '" style="width:60px;height:45px;object-fit:cover;border-radius:4px;flex-shrink:0" loading="lazy">';
      }
      h += '<div style="flex:1;min-width:0">';
      if (link) {
        h +=
          '<a href="' +
          escHTML(link) +
          '" target="_blank" rel="noopener" style="font-size:12px;font-weight:600;color:var(--text);line-height:1.3;text-decoration:none">' +
          escHTML(title) +
          '</a>';
      } else {
        h +=
          '<div style="font-size:12px;font-weight:600;color:var(--text);line-height:1.3">' +
          escHTML(title) +
          '</div>';
      }
      h +=
        '<div style="font-size:10px;color:var(--text-muted);margin-top:4px">';
      if (source) h += escHTML(source);
      if (source && published) h += ' · ';
      if (published) h += escHTML(published);
      h += '</div></div></div>';
    }
    mount.innerHTML =
      h || '<div class="ed-stats-loading">No news available.</div>';
  } catch {
    mount.innerHTML =
      '<div class="ed-stats-loading">Failed to load news.</div>';
  }
}

function extractArticles(raw: unknown): NewsArticle[] {
  if (!raw || typeof raw !== 'object') return [];
  const r = raw as { DATA?: NewsArticle[]; data?: NewsArticle[] };
  const arr = r.DATA || r.data || [];
  return Array.isArray(arr) ? arr : [];
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
