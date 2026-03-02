"use client";

import { useEffect, useState } from "react";
import { api, Settings, Recommendation } from "@/lib/api";

function Toggle({
  label,
  enabled,
  onChange,
}: {
  label: string;
  enabled: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between py-3" style={{ borderBottom: "1px solid #2a2a2a" }}>
      <span className="text-sm">{label}</span>
      <button
        onClick={() => onChange(!enabled)}
        className="relative w-12 h-6 rounded-full transition-colors"
        style={{ background: enabled ? "#00d4aa" : "#333" }}
      >
        <span
          className="absolute top-1 w-4 h-4 rounded-full bg-white transition-transform"
          style={{ transform: enabled ? "translateX(28px)" : "translateX(4px)" }}
        />
      </button>
    </div>
  );
}

export default function ControlsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [maxPos, setMaxPos] = useState(15);
  const [dailyLoss, setDailyLoss] = useState(3);
  const [sizingMode, setSizingMode] = useState<"fixed_dollar" | "percentage">("fixed_dollar");
  const [fixedAmount, setFixedAmount] = useState("5");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [denyingId, setDenyingId] = useState<string | null>(null);
  const [denyReason, setDenyReason] = useState("");
  const [history, setHistory] = useState<Recommendation[]>([]);
  const [showHistory, setShowHistory] = useState(false);

  useEffect(() => {
    Promise.all([api.getSettings(), api.getRecommendations()])
      .then(([s, recs]) => {
        setSettings(s);
        setMaxPos(Math.round(parseFloat(s.max_position_pct || "0.15") * 100));
        setDailyLoss(Math.round(parseFloat(s.daily_loss_limit_pct || "0.03") * 100));
        setSizingMode((s.sizing_mode as "fixed_dollar" | "percentage") || "fixed_dollar");
        setFixedAmount(s.fixed_trade_amount || "5");
        setRecommendations(recs);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  const botEnabled = settings?.bot_enabled === "true";

  const handleApprove = async (id: string) => {
    try {
      await api.approveRecommendation(id);
      setRecommendations((prev) => prev.filter((r) => r.id !== id));
      // Refresh settings since a value changed
      const s = await api.getSettings();
      setSettings(s);
      setMaxPos(Math.round(parseFloat(s.max_position_pct || "0.15") * 100));
      setDailyLoss(Math.round(parseFloat(s.daily_loss_limit_pct || "0.03") * 100));
    } catch (e) {
      setError(String(e));
    }
  };

  const handleDeny = async (id: string) => {
    if (!denyReason.trim()) return;
    try {
      await api.denyRecommendation(id, denyReason.trim());
      setRecommendations((prev) => prev.filter((r) => r.id !== id));
      setDenyingId(null);
      setDenyReason("");
    } catch (e) {
      setError(String(e));
    }
  };

  const loadHistory = async () => {
    if (!showHistory) {
      try {
        const all = await api.getRecommendations("all");
        setHistory(all.filter((r) => r.status !== "pending"));
      } catch (e) {
        setError(String(e));
      }
    }
    setShowHistory(!showHistory);
  };

  const triggerLabel = (trigger: string) => {
    switch (trigger) {
      case "weekly_report": return "Weekly";
      case "consecutive_losses": return "3 Losses";
      case "cumulative_losses": return "10 Losses";
      default: return trigger;
    }
  };

  const toggleBot = async () => {
    if (!settings) return;
    const fn = botEnabled ? api.pauseBot : api.resumeBot;
    await fn();
    setSettings({ ...settings, bot_enabled: botEnabled ? "false" : "true" });
  };

  const saveSettings = async () => {
    setSaving(true);
    await api.updateSettings({
      max_position_pct: maxPos / 100,
      daily_loss_limit_pct: dailyLoss / 100,
      sizing_mode: sizingMode,
      fixed_trade_amount: parseFloat(fixedAmount) || 5,
    });
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  if (loading) {
    return (
      <div>
        <h1 className="text-xl font-bold mb-4">Controls</h1>
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="skeleton rounded-xl mb-3" style={{ height: 48 }} />
        ))}
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-xl font-bold mb-4">Controls</h1>

      {error && (
        <div
          className="rounded-xl p-3 mb-4 text-sm"
          style={{ background: "#ff444422", border: "1px solid #ff444455" }}
        >
          {error}
        </div>
      )}

      {/* Master kill switch */}
      <div
        className="rounded-xl p-4 mb-4"
        style={{ background: "#1a1a1a", border: "1px solid #2a2a2a" }}
      >
        <div className="flex items-center justify-between mb-2">
          <div>
            <p className="text-sm font-bold">
              BOT STATUS:{" "}
              <span style={{ color: botEnabled ? "#00d4aa" : "#ff4444" }}>
                {botEnabled ? "ACTIVE" : "PAUSED"}
              </span>
            </p>
            <p className="text-xs mt-0.5" style={{ color: "#555" }}>
              {botEnabled ? "Scanning and trading normally" : "All trading halted"}
            </p>
          </div>
          <button
            onClick={toggleBot}
            className="px-4 py-2 rounded-lg text-sm font-bold transition-colors"
            style={{
              background: botEnabled ? "#ff444422" : "#00d4aa22",
              border: `1px solid ${botEnabled ? "#ff4444" : "#00d4aa"}`,
              color: botEnabled ? "#ff4444" : "#00d4aa",
            }}
          >
            {botEnabled ? "PAUSE" : "RESUME"}
          </button>
        </div>
      </div>

      {/* Strategy toggles */}
      <div
        className="rounded-xl px-4 mb-4"
        style={{ background: "#1a1a1a", border: "1px solid #2a2a2a" }}
      >
        <p className="text-xs uppercase tracking-wide pt-4 pb-2" style={{ color: "#555" }}>
          Strategies
        </p>
        <Toggle
          label="Bond Strategy"
          enabled={settings?.bond_strategy_enabled === "true"}
          onChange={async (v) => {
            if (!settings) return;
            await api.toggleStrategy("bond_strategy_enabled", v);
            setSettings({ ...settings, bond_strategy_enabled: v ? "true" : "false" });
          }}
        />
        <Toggle
          label="Market Making"
          enabled={settings?.market_making_enabled === "true"}
          onChange={async (v) => {
            if (!settings) return;
            await api.toggleStrategy("market_making_enabled", v);
            setSettings({ ...settings, market_making_enabled: v ? "true" : "false" });
          }}
        />
        <Toggle
          label="BTC 15-Min"
          enabled={settings?.btc_strategy_enabled === "true"}
          onChange={async (v) => {
            if (!settings) return;
            await api.toggleStrategy("btc_strategy_enabled", v);
            setSettings({ ...settings, btc_strategy_enabled: v ? "true" : "false" });
          }}
        />
        <Toggle
          label="Weather"
          enabled={settings?.weather_strategy_enabled === "true"}
          onChange={async (v) => {
            if (!settings) return;
            await api.toggleStrategy("weather_strategy_enabled", v);
            setSettings({ ...settings, weather_strategy_enabled: v ? "true" : "false" });
          }}
        />
        <div className="pb-1" />
      </div>

      {/* Recommendations */}
      <div
        className="rounded-xl px-4 mb-4"
        style={{ background: "#1a1a1a", border: "1px solid #2a2a2a" }}
      >
        <p className="text-xs uppercase tracking-wide pt-4 pb-2" style={{ color: "#555" }}>
          Parameter Recommendations
        </p>
        {recommendations.length === 0 ? (
          <p className="text-xs pb-4" style={{ color: "#444" }}>
            No pending recommendations
          </p>
        ) : (
          recommendations.map((rec) => (
            <div
              key={rec.id}
              className="py-3"
              style={{ borderBottom: "1px solid #2a2a2a" }}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-bold" style={{ color: "#00d4aa" }}>
                  {rec.setting_key}
                </span>
                <span
                  className="text-xs px-2 py-0.5 rounded-full"
                  style={{ background: "#ff8c0022", color: "#ff8c00", border: "1px solid #ff8c0044" }}
                >
                  {triggerLabel(rec.trigger)}
                </span>
              </div>
              <p className="text-sm mb-2">
                {rec.current_value}{" "}
                <span style={{ color: "#ff8c00" }}>&rarr;</span>{" "}
                <span style={{ color: "#00d4aa" }}>{rec.proposed_value}</span>
              </p>
              <p className="text-xs mb-3" style={{ color: "#888" }}>
                {rec.reasoning}
              </p>
              {denyingId === rec.id ? (
                <div>
                  <input
                    type="text"
                    placeholder="Why deny this change?"
                    value={denyReason}
                    onChange={(e) => setDenyReason(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg text-sm mb-2"
                    style={{
                      background: "#111",
                      border: "1px solid #2a2a2a",
                      color: "#fff",
                      outline: "none",
                    }}
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleDeny(rec.id)}
                      disabled={!denyReason.trim()}
                      className="flex-1 py-2 rounded-lg text-sm font-bold"
                      style={{
                        background: denyReason.trim() ? "#ff444422" : "#111",
                        border: `1px solid ${denyReason.trim() ? "#ff4444" : "#2a2a2a"}`,
                        color: denyReason.trim() ? "#ff4444" : "#444",
                      }}
                    >
                      Confirm Deny
                    </button>
                    <button
                      onClick={() => { setDenyingId(null); setDenyReason(""); }}
                      className="flex-1 py-2 rounded-lg text-sm font-bold"
                      style={{ background: "#111", border: "1px solid #2a2a2a", color: "#666" }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex gap-2">
                  <button
                    onClick={() => handleApprove(rec.id)}
                    className="flex-1 py-2 rounded-lg text-sm font-bold"
                    style={{ background: "#00d4aa22", border: "1px solid #00d4aa", color: "#00d4aa" }}
                  >
                    Approve
                  </button>
                  <button
                    onClick={() => setDenyingId(rec.id)}
                    className="flex-1 py-2 rounded-lg text-sm font-bold"
                    style={{ background: "#ff444422", border: "1px solid #ff4444", color: "#ff4444" }}
                  >
                    Deny
                  </button>
                </div>
              )}
            </div>
          ))
        )}
        <button
          onClick={loadHistory}
          className="w-full py-2 text-xs font-bold"
          style={{ color: "#555" }}
        >
          {showHistory ? "Hide History" : "Show History"}
        </button>
        {showHistory && (
          history.length === 0 ? (
            <p className="text-xs pb-3" style={{ color: "#444" }}>
              No past recommendations
            </p>
          ) : (
            history.map((rec) => (
              <div
                key={rec.id}
                className="py-2"
                style={{ borderTop: "1px solid #2a2a2a" }}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-bold" style={{ color: "#666" }}>
                    {rec.setting_key}
                  </span>
                  <div className="flex items-center gap-2">
                    <span
                      className="text-xs px-2 py-0.5 rounded-full"
                      style={{ background: "#ff8c0022", color: "#ff8c00", border: "1px solid #ff8c0044" }}
                    >
                      {triggerLabel(rec.trigger)}
                    </span>
                    <span
                      className="text-xs px-2 py-0.5 rounded-full"
                      style={{
                        background: rec.status === "approved" ? "#00d4aa22" : "#ff444422",
                        color: rec.status === "approved" ? "#00d4aa" : "#ff4444",
                        border: `1px solid ${rec.status === "approved" ? "#00d4aa44" : "#ff444444"}`,
                      }}
                    >
                      {rec.status}
                    </span>
                  </div>
                </div>
                <p className="text-xs" style={{ color: "#555" }}>
                  {rec.current_value} &rarr; {rec.proposed_value}
                </p>
                {rec.denial_reason && (
                  <p className="text-xs mt-1" style={{ color: "#ff4444" }}>
                    Denied: {rec.denial_reason}
                  </p>
                )}
              </div>
            ))
          )
        )}
        <div className="pb-1" />
      </div>

      {/* Sizing mode */}
      <div
        className="rounded-xl p-4 mb-4"
        style={{ background: "#1a1a1a", border: "1px solid #2a2a2a" }}
      >
        <p className="text-xs uppercase tracking-wide mb-4" style={{ color: "#555" }}>
          Trade Sizing
        </p>

        <div className="flex gap-2 mb-4">
          {(["fixed_dollar", "percentage"] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setSizingMode(mode)}
              className="flex-1 py-2 rounded-lg text-sm font-bold transition-colors"
              style={{
                background: sizingMode === mode ? "#00d4aa22" : "#111",
                border: `1px solid ${sizingMode === mode ? "#00d4aa" : "#2a2a2a"}`,
                color: sizingMode === mode ? "#00d4aa" : "#666",
              }}
            >
              {mode === "fixed_dollar" ? "Fixed $" : "% of Bankroll"}
            </button>
          ))}
        </div>

        {sizingMode === "fixed_dollar" && (
          <div>
            <div className="flex justify-between text-sm mb-2">
              <span>Amount per trade</span>
              <span style={{ color: "#00d4aa" }}>${fixedAmount}</span>
            </div>
            <input
              type="number"
              min={1}
              max={100}
              step={1}
              value={fixedAmount}
              onChange={(e) => setFixedAmount(e.target.value)}
              className="w-full px-3 py-2 rounded-lg text-sm"
              style={{
                background: "#111",
                border: "1px solid #2a2a2a",
                color: "#fff",
                outline: "none",
              }}
            />
            <p className="text-xs mt-2" style={{ color: "#555" }}>
              Each trade uses ${fixedAmount} → contracts = floor(${fixedAmount} / entry price)
            </p>
          </div>
        )}

        {sizingMode === "percentage" && (
          <p className="text-xs" style={{ color: "#555" }}>
            Uses strategy-proposed size, clamped by max position % below
          </p>
        )}
      </div>

      {/* Risk settings */}
      <div
        className="rounded-xl p-4 mb-4"
        style={{ background: "#1a1a1a", border: "1px solid #2a2a2a" }}
      >
        <p className="text-xs uppercase tracking-wide mb-4" style={{ color: "#555" }}>
          Risk Settings
        </p>

        <div className="mb-4">
          <div className="flex justify-between text-sm mb-2">
            <span>Max Position Size</span>
            <span style={{ color: "#00d4aa" }}>{maxPos}%</span>
          </div>
          <input
            type="range"
            min={5}
            max={25}
            value={maxPos}
            onChange={(e) => setMaxPos(Number(e.target.value))}
            className="w-full accent-green-400"
            style={{ accentColor: "#00d4aa" }}
          />
          <div className="flex justify-between text-xs mt-1" style={{ color: "#444" }}>
            <span>5%</span>
            <span>25%</span>
          </div>
        </div>

        <div className="mb-4">
          <div className="flex justify-between text-sm mb-2">
            <span>Daily Loss Limit</span>
            <span style={{ color: "#ff8c00" }}>{dailyLoss}%</span>
          </div>
          <input
            type="range"
            min={1}
            max={25}
            value={dailyLoss}
            onChange={(e) => setDailyLoss(Number(e.target.value))}
            className="w-full"
            style={{ accentColor: "#ff8c00" }}
          />
          <div className="flex justify-between text-xs mt-1" style={{ color: "#444" }}>
            <span>1%</span>
            <span>25%</span>
          </div>
        </div>

        <button
          onClick={saveSettings}
          disabled={saving}
          className="w-full py-3 rounded-lg text-sm font-bold transition-all"
          style={{
            background: saved ? "#00d4aa22" : "#00d4aa",
            color: saved ? "#00d4aa" : "#000",
            border: saved ? "1px solid #00d4aa" : "none",
          }}
        >
          {saving ? "Saving…" : saved ? "✓ Saved" : "Save Settings"}
        </button>
      </div>

      {/* Last updated */}
      <p className="text-xs text-center pb-4" style={{ color: "#333" }}>
        Settings loaded from database
      </p>
    </div>
  );
}
