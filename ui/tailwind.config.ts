import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#0a0a0a",
        card: "#1a1a1a",
        border: "#2a2a2a",
        green: "#00d4aa",
        red: "#ff4444",
        orange: "#ff8c00",
      },
    },
  },
  plugins: [],
};

export default config;
