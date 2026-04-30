/**
 * Fetch helper for the /normalized endpoint. Single source of truth
 * for the Detailed Event Stats panel. Block components consume this
 * shape, never raw FL responses.
 */
import type { NormalizedEvent } from '../types/normalized';

export interface NormalizedOpts {
  refresh?: boolean;
  topScorersLimit?: number;
}

export async function fetchNormalized(
  ticker: string,
  opts: NormalizedOpts = {},
): Promise<NormalizedEvent> {
  const params = new URLSearchParams();
  if (opts.refresh) params.set('refresh', '1');
  if (opts.topScorersLimit !== undefined) {
    params.set('top_scorers_limit', String(opts.topScorersLimit));
  }
  const qs = params.toString();
  const url =
    `/api/event/${encodeURIComponent(ticker)}/normalized` +
    (qs ? '?' + qs : '');
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`/normalized ${res.status}`);
  }
  const data = await res.json();
  if (data && typeof data === 'object' && 'error' in data) {
    throw new Error(String((data as { error: unknown }).error));
  }
  return data as NormalizedEvent;
}
