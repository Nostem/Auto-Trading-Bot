"use client";

import { useState } from "react";
import type { Trade } from "@/lib/api";

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const strategyColors: Record<string, string> = {
  bond: "#00d4aa",
  market_making: "#ff8c00",
  news_arbitrage: "#a78bfa",
};

export default function TradeCard({ trade }: { trade: Trade }) {
  const [expanded, setExpanded] = useState(false);
  const pnl = trade.net_pnl;
  const isWin = pnl !== null && pnl > 0;
  const isLoss = pnl !== null && pnl < 0;

  return (
    <div
      className="rounded-xl p-4 cursor-pointer transition-all"
      style={{ background: "#1a1a1a", border: "1px solid #2a2a2a", marginBottom: 8 }}
      onClick={() => setExpanded(!expanded)}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium truncate">{trade.market_title}</p>
          <div className="flex items-center gap-2 mt-1">
            <span
              className="text-xs px-2 py-0.5 rounded-full font-medium"
              style={{
                background: strategyColors[trade.strategy] + "22",
                color: strategyColors[trade.strategy],
              }}
            >
              {trade.strategy.replace("_", " ")}
            </span>
            <span
              className="text-xs px-2 py-0.5 rounded-full"
              style={{ background: "#2a2a2a", color: "#aaa" }}
            >
              {trade.side.toUpperCase()}
            </span>
            <span className="text-xs" style={{ color: "#555" }}>
              {timeAgo(trade.created_at)}
            </span>
          </div>
        </div>
        <div className="text-right shrink-0">
          {pnl !== null ? (
            <span
              className="text-sm font-bold"
              style={{ color: isWin ? "#00d4aa" : isLoss ? "#ff4444" : "#666" }}
            >
              {isWin ? "+" : ""}${pnl.toFixed(2)}
            </span>
          ) : (
            <span className="text-xs" style={{ color: "#ff8c00" }}>
              Open
            </span>
          )}
          <p className="text-xs mt-0.5" style={{ color: "#555" }}>
            {trade.status}
          </p>
        </div>
      </div>

      {expanded && (
        <div className="mt-3 pt-3" style={{ borderTop: "1px solid #2a2a2a" }}>
          <div className="grid grid-cols-2 gap-2 text-xs mb-2">
            <div>
              <span style={{ color: "#555" }}>Entry: </span>
              <span>{(trade.entry_price * 100).toFixed(0)}¢</span>
            </div>
            {trade.exit_price != null && (
              <div>
                <span style={{ color: "#555" }}>Exit: </span>
                <span>{(trade.exit_price * 100).toFixed(0)}¢</span>
              </div>
            )}
            <div>
              <span style={{ color: "#555" }}>Size: </span>
              <span>{trade.size} contracts</span>
            </div>
          </div>
          {trade.entry_reasoning && (
            <p className="text-xs mb-2" style={{ color: "#888" }}>
              {trade.entry_reasoning}
            </p>
          )}
          {trade.reflection && (
            <div
              className="rounded-lg p-3 text-xs"
              style={{ background: "#111", border: "1px solid #2a2a2a" }}
            >
              <p className="font-medium mb-1" style={{ color: "#00d4aa" }}>
                AI Reflection
              </p>
              <p style={{ color: "#aaa" }}>{trade.reflection.summary}</p>
              {trade.reflection.strategy_suggestion && (
                <p className="mt-1" style={{ color: "#ff8c00" }}>
                  💡 {trade.reflection.strategy_suggestion}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
