import type { Position } from "@/lib/api";

const strategyColors: Record<string, string> = {
  bond: "#00d4aa",
  market_making: "#ff8c00",
  news_arbitrage: "#a78bfa",
};

export default function PositionCard({ position }: { position: Position }) {
  const pnl = position.unrealized_pnl;
  const isUp = pnl !== null && pnl > 0;
  const isDown = pnl !== null && pnl < 0;

  const currentPrice = position.current_price ?? position.entry_price;
  const priceMove = ((currentPrice - position.entry_price) / position.entry_price) * 100;

  return (
    <div
      className="rounded-xl p-4"
      style={{
        background: "#1a1a1a",
        border: `1px solid ${isDown ? "#ff444433" : "#2a2a2a"}`,
        marginBottom: 8,
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium truncate">{position.market_title}</p>
          <div className="flex items-center gap-2 mt-1">
            <span
              className="text-xs px-2 py-0.5 rounded-full"
              style={{
                background: (strategyColors[position.strategy] || "#666") + "22",
                color: strategyColors[position.strategy] || "#666",
              }}
            >
              {position.strategy.replace("_", " ")}
            </span>
            <span
              className="text-xs px-2 py-0.5 rounded-full"
              style={{ background: "#2a2a2a", color: "#aaa" }}
            >
              {position.side.toUpperCase()}
            </span>
          </div>
        </div>
        <div className="text-right shrink-0">
          <span
            className="text-sm font-bold"
            style={{ color: isUp ? "#00d4aa" : isDown ? "#ff4444" : "#aaa" }}
          >
            {isUp ? "+" : ""}
            {pnl !== null ? `$${pnl.toFixed(2)}` : "—"}
          </span>
          <p className="text-xs mt-0.5" style={{ color: "#555" }}>
            {priceMove >= 0 ? "+" : ""}
            {priceMove.toFixed(1)}%
          </p>
        </div>
      </div>

      <div className="flex gap-4 mt-2 text-xs" style={{ color: "#555" }}>
        <span>Entry: {(position.entry_price * 100).toFixed(0)}¢</span>
        <span>Current: {(currentPrice * 100).toFixed(0)}¢</span>
        <span>{position.size} contracts</span>
      </div>
    </div>
  );
}
