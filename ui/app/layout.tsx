import type { Metadata } from "next";
import "./globals.css";
import BottomNav from "@/components/BottomNav";

export const metadata: Metadata = {
  title: "Kalshi Bot",
  description: "Prediction market trading bot dashboard",
  manifest: "/manifest.json",
  themeColor: "#0a0a0a",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Kalshi Bot",
  },
  viewport: {
    width: "device-width",
    initialScale: 1,
    maximumScale: 1,
    userScalable: false,
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-background text-white min-h-screen pb-20">
        <main className="max-w-[390px] mx-auto px-4 pt-4">{children}</main>
        <BottomNav />
      </body>
    </html>
  );
}
