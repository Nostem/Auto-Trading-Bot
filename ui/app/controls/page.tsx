"use client";

import { useEffect, useState } from "react";
import { api, Settings } from "@/lib/api";

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
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getSettings().then((s) => {
      setSettings(s);
      setMaxPos(Math.round(parseFloat(s.max_position_pct) * 100));
      setDailyLoss(Math.round(parseFloat(s.daily_loss_limit_pct) * 100));
      setLoading(false);
    });
  }, []);

  const botEnabled = settings?.bot_enabled === "true";

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
            await api.updateSettings({
              max_position_pct: maxPos / 100,
              daily_loss_limit_pct: dailyLoss / 100,
            });
            setSettings({ ...settings, bond_strategy_enabled: v ? "true" : "false" });
          }}
        />
        <Toggle
          label="Market Making"
          enabled={settings?.market_making_enabled === "true"}
          onChange={async (v) => {
            if (!settings) return;
            setSettings({ ...settings, market_making_enabled: v ? "true" : "false" });
          }}
        />
        <Toggle
          label="News Arbitrage"
          enabled={settings?.news_arbitrage_enabled === "true"}
          onChange={async (v) => {
            if (!settings) return;
            setSettings({ ...settings, news_arbitrage_enabled: v ? "true" : "false" });
          }}
        />
        <div className="pb-1" />
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
            max={10}
            value={dailyLoss}
            onChange={(e) => setDailyLoss(Number(e.target.value))}
            className="w-full"
            style={{ accentColor: "#ff8c00" }}
          />
          <div className="flex justify-between text-xs mt-1" style={{ color: "#444" }}>
            <span>1%</span>
            <span>10%</span>
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
