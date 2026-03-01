const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";
const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN || "";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${API_TOKEN}`,
      ...(options?.headers ?? {}),
    },
  });

  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${await res.text()}`);
  }

  return res.json() as Promise<T>;
}

// ---- Types ----

export interface DashboardData {
  bankroll: number;
  total_pnl: number;
  today_pnl: number;
  win_rate: number;
  total_trades: number;
  open_positions: number;
  unrealized_pnl: number;
  best_strategy: string;
  streak: number;
}

export interface Trade {
  id: string;
  market_id: string;
  market_title: string;
  strategy: string;
  side: string;
  size: number;
  entry_price: number;
  exit_price: number | null;
  net_pnl: number | null;
  status: string;
  entry_reasoning: string | null;
  created_at: string;
  resolved_at: string | null;
  reflection?: {
    summary: string;
    what_worked: string | null;
    what_failed: string | null;
    confidence_score: number | null;
    strategy_suggestion: string | null;
  };
}

export interface Position {
  id: string;
  market_id: string;
  market_title: string;
  strategy: string;
  side: string;
  size: number;
  entry_price: number;
  current_price: number | null;
  unrealized_pnl: number | null;
  opened_at: string;
}

export interface Reflection {
  id: string;
  trade_id: string | null;
  summary: string;
  what_worked: string | null;
  what_failed: string | null;
  confidence_score: number | null;
  strategy_suggestion: string | null;
  created_at: string;
}

export interface WeeklyReflection {
  id: string;
  week_start: string;
  week_end: string;
  total_trades: number | null;
  win_rate: number | null;
  net_pnl: number | null;
  top_strategy: string | null;
  summary: string | null;
  key_learnings: string | null;
  created_at: string;
}

export interface Recommendation {
  id: string;
  setting_key: string;
  current_value: string;
  proposed_value: string;
  reasoning: string;
  trigger: string;
  status: string;
  denial_reason: string | null;
  created_at: string;
}

export interface Settings {
  bot_enabled: string;
  bond_strategy_enabled: string;
  market_making_enabled: string;
  btc_strategy_enabled: string;
  max_position_pct: string;
  daily_loss_limit_pct: string;
  current_bankroll: string;
  sizing_mode: string;
  fixed_trade_amount: string;
  [key: string]: string;  // allow extra settings from DB
}

// ---- API calls ----

export const api = {
  getDashboard: () => apiFetch<DashboardData>("/dashboard"),
  getTrades: (params?: { page?: number; strategy?: string; status?: string }) => {
    const q = new URLSearchParams({
      page: String(params?.page ?? 1),
      strategy: params?.strategy ?? "all",
      status: params?.status ?? "all",
    });
    return apiFetch<{ trades: Trade[]; total: number; page: number; pages: number }>(
      `/trades?${q}`
    );
  },
  getTrade: (id: string) => apiFetch<Trade>(`/trades/${id}`),
  getPositions: () => apiFetch<Position[]>("/positions"),
  getReflections: (page = 1) =>
    apiFetch<{ reflections: Reflection[]; total: number; page: number; pages: number }>(
      `/reflections?page=${page}`
    ),
  getWeeklyReflections: () => apiFetch<WeeklyReflection[]>("/reflections/weekly"),
  getSettings: () => apiFetch<Settings>("/controls/settings"),
  pauseBot: () => apiFetch("/controls/pause", { method: "POST" }),
  resumeBot: () => apiFetch("/controls/resume", { method: "POST" }),
  toggleStrategy: (key: string, enabled: boolean) =>
    apiFetch("/controls/strategy", {
      method: "POST",
      body: JSON.stringify({ key, enabled }),
    }),
  updateSettings: (body: {
    max_position_pct: number;
    daily_loss_limit_pct: number;
    sizing_mode?: string;
    fixed_trade_amount?: number;
  }) => apiFetch("/controls/settings", { method: "POST", body: JSON.stringify(body) }),
  getRecommendations: (status = "pending") =>
    apiFetch<Recommendation[]>(`/controls/recommendations?status=${status}`),
  approveRecommendation: (id: string) =>
    apiFetch<{ status: string; setting_key: string; new_value: string }>(
      `/controls/recommendations/${id}/approve`,
      { method: "POST" }
    ),
  denyRecommendation: (id: string, reason: string) =>
    apiFetch<{ status: string; setting_key: string }>(
      `/controls/recommendations/${id}/deny`,
      { method: "POST", body: JSON.stringify({ reason }) }
    ),
};
