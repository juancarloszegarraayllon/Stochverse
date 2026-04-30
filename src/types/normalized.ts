/**
 * Type definitions for the /api/event/<ticker>/normalized response.
 *
 * Mirrors the shape produced by event_normalized() in main.py. Keep
 * in sync when backend fields change. Optional fields are nullable
 * because the backend uses null (not undefined) for absent data.
 */

export interface NormalizedEvent {
  ticker: string;
  fl_event_id: string;
  sport: string;
  format: 'single' | 'knockout' | 'league' | string;
  state: 'live' | 'final' | 'scheduled';
  title: string;
  league: { name: string; country: string };
  tournament_stage_id: string;
  tournament_season_id: string;
  bracket_stage_id: string;
  bracket_stage_name: string;
  participants: Array<{
    side: 'home' | 'away';
    name: string;
    abbrev: string;
    score: string;
  }>;
  scoreboard: {
    home_score: string;
    away_score: string;
    period: number;
    display_clock: string;
    clock_running: boolean | null;
    stage_start_ms: number;
  };
  capabilities: {
    has_summary: boolean;
    has_stats: boolean;
    has_lineups: boolean;
    has_predicted_lineups: boolean;
    has_player_stats: boolean;
    has_missing_players: boolean;
    has_commentary: boolean;
    has_h2h: boolean;
    has_news: boolean;
    has_odds: boolean;
    has_video: boolean;
    has_report: boolean;
    has_standings: boolean;
    has_bracket: boolean;
  };
  data: {
    bracket: BracketData | null;
    standings: {
      top_scorers: TopScorers | null;
      [key: string]: unknown;
    };
    [key: string]: unknown;
  };
}

export interface BracketData {
  rounds: BracketRound[];
}

export interface BracketRound {
  round_num: number;
  label: string | null;
  pairs: BracketPair[];
}

export interface BracketPair {
  home: string | null;
  away: string | null;
  home_name: string | null;
  away_name: string | null;
  home_team_id: string | null;
  away_team_id: string | null;
  legs: Array<{ home: number; away: number }>;
  winner: 'home' | 'away' | null;
  agg_home: number | null;
  agg_away: number | null;
  starts_at: number | null;
}

export interface TopScorers {
  rows: Array<{
    rank: number;
    name: string;
    team: string;
    goals: number;
    assists: number;
  }>;
  total: number;
  has_more: boolean;
}
