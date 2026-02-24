interface StatCardProps {
  label: string;
  value: string | number;
  positive?: boolean | null;
  small?: boolean;
}

export default function StatCard({ label, value, positive, small }: StatCardProps) {
  const valueColor =
    positive === true
      ? "#00d4aa"
      : positive === false
      ? "#ff4444"
      : "#fff";

  return (
    <div
      className="rounded-xl p-4 flex flex-col gap-1"
      style={{ background: "#1a1a1a", border: "1px solid #2a2a2a" }}
    >
      <span className="text-xs uppercase tracking-wide" style={{ color: "#666" }}>
        {label}
      </span>
      <span
        className={small ? "text-lg font-bold" : "text-2xl font-bold"}
        style={{ color: valueColor }}
      >
        {value}
      </span>
    </div>
  );
}
