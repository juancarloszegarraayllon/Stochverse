/**
 * Fetch helper for the legacy /api/event/<ticker>/h2h endpoint.
 *
 * Why a separate endpoint (not /normalized): H2H carries a deeply
 * nested FL-specific shape (TABS → GROUPS → ITEMS) that's distinct
 * from every other surface, plus the past-event fallback chain
 * for future fixtures (search team participant IDs → /v1/teams/
 * results → past matchup → /v1/events/h2h) lives in main.py's
 * get_event_h2h. Wrapping it again in /normalized would duplicate
 * the fallback logic without adding value.
 */

export interface H2HResponse {
  data: { DATA?: H2HTab[]; data?: H2HTab[] };
  home_name: string;
  away_name: string;
  source: string;
}

export interface H2HTab {
  TAB_NAME?: string;
  GROUPS?: Array<{
    GROUP_LABEL?: string;
    NAME?: string;
    ITEMS?: H2HItem[];
  }>;
}

export interface H2HItem {
  EVENT_ID?: string;
  HOME_PARTICIPANT_NAME_ONE?: string;
  AWAY_PARTICIPANT_NAME_ONE?: string;
  HOME_PARTICIPANT?: string;
  AWAY_PARTICIPANT?: string;
  HOME_SCORE_FULL?: string | number;
  AWAY_SCORE_FULL?: string | number;
  H_RESULT?: string;
  EVENT_NAME?: string;
  EVENT_ACRONYM?: string;
  STAGE?: string;
  HOME_IMAGES?: string[];
  AWAY_IMAGES?: string[];
  START_TIME?: number;
}

export async function fetchH2H(ticker: string): Promise<H2HResponse> {
  const res = await fetch(
    `/api/event/${encodeURIComponent(ticker)}/h2h`,
  );
  if (!res.ok) throw new Error(`/h2h ${res.status}`);
  const data = await res.json();
  if (data && typeof data === 'object' && 'error' in data) {
    throw new Error(String((data as { error: unknown }).error));
  }
  return data as H2HResponse;
}
