"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const tabs = [
  { href: "/", label: "Dashboard", icon: "⬛" },
  { href: "/trades", label: "Trades", icon: "📊" },
  { href: "/reflections", label: "Reflections", icon: "🤖" },
  { href: "/controls", label: "Controls", icon: "⚙️" },
];

export default function BottomNav() {
  const pathname = usePathname();

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50"
      style={{
        background: "#111",
        borderTop: "1px solid #2a2a2a",
        paddingBottom: "env(safe-area-inset-bottom, 0px)",
      }}
    >
      <div className="max-w-[390px] mx-auto flex">
        {tabs.map((tab) => {
          const active = pathname === tab.href;
          return (
            <Link
              key={tab.href}
              href={tab.href}
              className="flex-1 flex flex-col items-center py-3 gap-1 text-xs transition-colors"
              style={{ color: active ? "#00d4aa" : "#666" }}
            >
              <span className="text-lg leading-none">{tab.icon}</span>
              <span>{tab.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
