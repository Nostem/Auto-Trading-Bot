"use client";

import { useCallback, useEffect, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { api, DashboardData, Position } from "@/lib/api";
import StatCard from "@/components/StatCard";
import PositionCard from "@/components/PositionCard";

function Skeleton({ h = 20, w = "100%" }: { h?: number; w?: string }) {
  return <div className="skeleton" style={{ height: h, width: w }} />;
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [pnlHistory, setPnlHistory] = useState<{ day: string; pnl: number }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [dash, pos] = await Promise.all([
        api.getDashboard(),
        api.getPositions(),
      ]);
      setData(dash);
      setPositions(pos);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, [load]);

  const botActive = data !== null;
  const todayPnlPositive = data ? data.today_pnl >= 0 : null;

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold">Kalshi Bot</h1>
        <div className="flex items-center gap-2">
          <div
            className="w-2.5 h-2.5 rounded-full"
            style={{
              background: loading ? "#555" : botActive ? "#00d4aa" : "#ff4444",
              boxShadow: botActive ? "0 0 6px #00d4aa88" : undefined,
            }}
          />
          <span className="text-xs" style={{ color: "#666" }}>
            {loading ? "Connecting…" : botActive ? "Active" : "Paused"}
          </span>
        </div>
      </div>

      {error && (
        <div
          className="rounded-xl p-3 mb-4 text-sm"
          style={{ background: "#ff444422", border: "1px solid #ff444455" }}
        >
          {error}
        </div>
      )}

      {/* Bankroll card */}
      <div
        className="rounded-xl p-5 mb-4"
        style={{ background: "#1a1a1a", border: "1px solid #2a2a2a" }}
      >
        <p className="text-xs uppercase tracking-wide mb-1" style={{ color: "#666" }}>
          Bankroll
        </p>
        {loading ? (
          <Skeleton h={40} w="60%" />
        ) : (
          <>
            <p className="text-4xl font-bold">
              ${data?.bankroll.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </p>
            <p
              className="text-sm mt-1 font-medium"
              style={{ color: todayPnlPositive ? "#00d4aa" : "#ff4444" }}
            >
              {todayPnlPositive ? "+" : ""}
              ${data?.today_pnl.toFixed(2)} today
            </p>
          </>
        )}
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-2 mb-4">
        {loading ? (
          <>
            <Skeleton h={72} />
            <Skeleton h={72} />
            <Skeleton h={72} />
          </>
        ) : (
          <>
            <StatCard
              label="Win Rate"
              value={`${data?.win_rate ?? 0}%`}
              positive={(data?.win_rate ?? 0) >= 50}
              small
            />
            <StatCard
              label="Total PnL"
              value={`$${(data?.total_pnl ?? 0).toFixed(0)}`}
              positive={(data?.total_pnl ?? 0) > 0}
              small
            />
            <StatCard
              label="Positions"
              value={data?.open_positions ?? 0}
              small
            />
          </>
        )}
      </div>

      {/* Unrealized PnL + streak */}
      <div className="grid grid-cols-2 gap-2 mb-4">
        <StatCard
          label="Unrealized"
          value={loading ? "…" : `$${(data?.unrealized_pnl ?? 0).toFixed(2)}`}
          positive={loading ? null : (data?.unrealized_pnl ?? 0) > 0}
          small
        />
        <StatCard
          label="Streak"
          value={
            loading
              ? "…"
              : `${(data?.streak ?? 0) > 0 ? "+" : ""}${data?.streak ?? 0}`
          }
          positive={loading ? null : (data?.streak ?? 0) > 0}
          small
        />
      </div>

      {/* Open Positions */}
      {positions.length > 0 && (
        <div className="mb-2">
          <h2 className="text-sm font-semibold mb-2" style={{ color: "#888" }}>
            Open Positions ({positions.length})
          </h2>
          {positions.map((p) => (
            <PositionCard key={p.id} position={p} />
          ))}
        </div>
      )}
    </div>
  );
}
