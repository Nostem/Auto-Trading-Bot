"use client";

import { useEffect, useState } from "react";
import { api, Trade } from "@/lib/api";
import TradeCard from "@/components/TradeCard";

const STRATEGY_FILTERS = ["all", "bond", "market_making", "btc_15min"];
const STATUS_FILTERS = ["all", "open", "closed"];

function Skeleton() {
  return (
    <div
      className="skeleton rounded-xl"
      style={{ height: 72, marginBottom: 8 }}
    />
  );
}

export default function TradesPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [strategy, setStrategy] = useState("all");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .getTrades({ page, strategy, status })
      .then((res) => {
        setTrades(res.trades);
        setTotalPages(res.pages);
      })
      .finally(() => setLoading(false));
  }, [page, strategy, status]);

  const changeFilter = (type: "strategy" | "status", value: string) => {
    setPage(1);
    if (type === "strategy") setStrategy(value);
    else setStatus(value);
  };

  return (
    <div>
      <h1 className="text-xl font-bold mb-3">Trades</h1>

      {/* Strategy filter chips */}
      <div className="flex gap-2 overflow-x-auto pb-2 mb-2 no-scrollbar">
        {STRATEGY_FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => changeFilter("strategy", f)}
            className="shrink-0 text-xs px-3 py-1.5 rounded-full transition-colors"
            style={{
              background: strategy === f ? "#00d4aa22" : "#1a1a1a",
              border: `1px solid ${strategy === f ? "#00d4aa" : "#2a2a2a"}`,
              color: strategy === f ? "#00d4aa" : "#888",
            }}
          >
            {f === "all" ? "All" : f.replace("_", " ")}
          </button>
        ))}
      </div>

      {/* Status filter chips */}
      <div className="flex gap-2 mb-4">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => changeFilter("status", f)}
            className="text-xs px-3 py-1.5 rounded-full transition-colors"
            style={{
              background: status === f ? "#2a2a2a" : "#1a1a1a",
              border: `1px solid ${status === f ? "#555" : "#2a2a2a"}`,
              color: status === f ? "#fff" : "#888",
            }}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {/* Trade list */}
      {loading ? (
        Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} />)
      ) : trades.length === 0 ? (
        <p className="text-center py-12 text-sm" style={{ color: "#555" }}>
          No trades yet
        </p>
      ) : (
        trades.map((t) => <TradeCard key={t.id} trade={t} />)
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex justify-center items-center gap-4 mt-4">
          <button
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
            className="text-sm px-4 py-2 rounded-lg disabled:opacity-30"
            style={{ background: "#1a1a1a", border: "1px solid #2a2a2a" }}
          >
            ← Prev
          </button>
          <span className="text-xs" style={{ color: "#555" }}>
            {page} / {totalPages}
          </span>
          <button
            disabled={page === totalPages}
            onClick={() => setPage((p) => p + 1)}
            className="text-sm px-4 py-2 rounded-lg disabled:opacity-30"
            style={{ background: "#1a1a1a", border: "1px solid #2a2a2a" }}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}
