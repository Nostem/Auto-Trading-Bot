import type { Reflection, WeeklyReflection } from "@/lib/api";

function scoreColor(score: number | null): string {
  if (score === null) return "#666";
  if (score >= 8) return "#00d4aa";
  if (score >= 5) return "#ff8c00";
  return "#ff4444";
}

export function ReflectionCard({ reflection }: { reflection: Reflection }) {
  return (
    <div
      className="rounded-xl p-4"
      style={{ background: "#1a1a1a", border: "1px solid #2a2a2a", marginBottom: 8 }}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <p className="text-sm font-medium flex-1">
          {reflection.trade_id ? "Trade Reflection" : "Reflection"}
        </p>
        <div className="flex items-center gap-1.5 shrink-0">
          <span
            className="text-xs font-bold px-2 py-0.5 rounded-full"
            style={{
              background: scoreColor(reflection.confidence_score) + "22",
              color: scoreColor(reflection.confidence_score),
            }}
          >
            {reflection.confidence_score ?? "—"}/10
          </span>
        </div>
      </div>

      <p className="text-sm mb-2" style={{ color: "#ccc" }}>
        {reflection.summary}
      </p>

      {reflection.what_worked && (
        <p className="text-xs mb-1" style={{ color: "#00d4aa" }}>
          ✓ {reflection.what_worked}
        </p>
      )}
      {reflection.what_failed && (
        <p className="text-xs mb-1" style={{ color: "#ff4444" }}>
          ✗ {reflection.what_failed}
        </p>
      )}
      {reflection.strategy_suggestion && (
        <p className="text-xs mt-2" style={{ color: "#ff8c00" }}>
          💡 {reflection.strategy_suggestion}
        </p>
      )}

      <p className="text-xs mt-2" style={{ color: "#444" }}>
        {new Date(reflection.created_at).toLocaleDateString()}
      </p>
    </div>
  );
}

export function WeeklyReflectionCard({ report }: { report: WeeklyReflection }) {
  return (
    <div
      className="rounded-xl p-4"
      style={{
        background: "#1a1a1a",
        border: "1px solid #2a2a2a",
        marginBottom: 8,
      }}
    >
      <div className="flex items-center justify-between mb-3">
        <div>
          <p className="text-sm font-bold">Weekly Report</p>
          <p className="text-xs" style={{ color: "#555" }}>
            {report.week_start} – {report.week_end}
          </p>
        </div>
        {report.net_pnl !== null && (
          <span
            className="text-lg font-bold"
            style={{ color: report.net_pnl >= 0 ? "#00d4aa" : "#ff4444" }}
          >
            {report.net_pnl >= 0 ? "+" : ""}${report.net_pnl.toFixed(2)}
          </span>
        )}
      </div>

      <div className="flex gap-3 text-xs mb-3" style={{ color: "#888" }}>
        {report.total_trades !== null && <span>{report.total_trades} trades</span>}
        {report.win_rate !== null && <span>{report.win_rate.toFixed(1)}% win</span>}
        {report.top_strategy && <span>Best: {report.top_strategy}</span>}
      </div>

      {report.summary && (
        <p className="text-sm mb-2" style={{ color: "#ccc" }}>
          {report.summary}
        </p>
      )}
      {report.key_learnings && (
        <div
          className="rounded-lg p-3 text-xs"
          style={{ background: "#111", border: "1px solid #2a2a2a" }}
        >
          <p className="font-medium mb-1" style={{ color: "#ff8c00" }}>
            Key Learnings
          </p>
          <p style={{ color: "#aaa" }}>{report.key_learnings}</p>
        </div>
      )}
    </div>
  );
}
