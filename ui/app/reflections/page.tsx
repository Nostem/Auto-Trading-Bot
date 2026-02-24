"use client";

import { useEffect, useState } from "react";
import { api, Reflection, WeeklyReflection } from "@/lib/api";
import { ReflectionCard, WeeklyReflectionCard } from "@/components/ReflectionCard";

function Skeleton() {
  return (
    <div
      className="skeleton rounded-xl"
      style={{ height: 120, marginBottom: 8 }}
    />
  );
}

export default function ReflectionsPage() {
  const [tab, setTab] = useState<"trade" | "weekly">("trade");
  const [reflections, setReflections] = useState<Reflection[]>([]);
  const [weekly, setWeekly] = useState<WeeklyReflection[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    if (tab === "trade") {
      api
        .getReflections()
        .then((res) => setReflections(res.reflections))
        .finally(() => setLoading(false));
    } else {
      api
        .getWeeklyReflections()
        .then(setWeekly)
        .finally(() => setLoading(false));
    }
  }, [tab]);

  return (
    <div>
      <h1 className="text-xl font-bold mb-4">AI Reflections</h1>

      {/* Tab toggle */}
      <div
        className="flex rounded-xl p-1 mb-4"
        style={{ background: "#1a1a1a", border: "1px solid #2a2a2a" }}
      >
        {(["trade", "weekly"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className="flex-1 py-2 rounded-lg text-sm font-medium transition-colors"
            style={{
              background: tab === t ? "#2a2a2a" : "transparent",
              color: tab === t ? "#fff" : "#666",
            }}
          >
            {t === "trade" ? "Trade Reflections" : "Weekly Reports"}
          </button>
        ))}
      </div>

      {loading ? (
        Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} />)
      ) : tab === "trade" ? (
        reflections.length === 0 ? (
          <p className="text-center py-12 text-sm" style={{ color: "#555" }}>
            No reflections yet — complete some trades first
          </p>
        ) : (
          reflections.map((r) => <ReflectionCard key={r.id} reflection={r} />)
        )
      ) : weekly.length === 0 ? (
        <p className="text-center py-12 text-sm" style={{ color: "#555" }}>
          No weekly reports yet
        </p>
      ) : (
        weekly.map((r) => <WeeklyReflectionCard key={r.id} report={r} />)
      )}
    </div>
  );
}
